"""Managed upstream platforms and tenant-scoped device snapshots."""
import threading
import uuid
from datetime import datetime, timezone
from functools import wraps
from urllib.parse import urlsplit

from flask import jsonify, request
from sqlalchemy import select, insert, update, func, or_, case
from werkzeug.exceptions import HTTPException

from integrations.smarteye.runtime import actor, execution, get_runtime, ContextExecutor, tracked_run, jobs
from integrations.smarteye.management_schema import platform_status, devices
from integrations.smarteye.api.db_utils import list_platform_configs, write_platform_config
from integrations.smarteye.api.smart_search import login_platform
from services.scene_categories import has_scene_labels,scene_result_status

executor=ContextExecutor(max_workers=2)
_sync_slots=threading.BoundedSemaphore(2)
_batch_slots=threading.BoundedSemaphore(2)
_check_batch_slots=threading.BoundedSemaphore(1)
_connection_slots=threading.BoundedSemaphore(2)
PLATFORM_FIELDS=('plat_id','platform_name','base_url','username','login_url','state')
ERRORS={'timeout':'连接超时，请检查网络或平台服务', 'connection':'无法连接，请检查地址和平台服务',
        'captcha':'验证码识别或校验失败，请重试或检查识别结果', 'authentication':'登录失败，请检查账号或权限',
        'response':'平台响应异常，请检查服务配置'}


def now():return datetime.now(timezone.utc).isoformat()


def list_platforms_internal():
    return [dict(row) for row in list_platform_configs() if row.get('state')!='deleted']


def get_platform(plat_id):
    return next((row for row in list_platforms_internal() if row['plat_id']==plat_id),None)


def _status(tenant_id,plat_id,changes=None):
    with get_runtime().lock,get_runtime().engine.begin() as conn:
        where=(platform_status.c.tenant_id==tenant_id,platform_status.c.plat_id==plat_id)
        old=conn.execute(select(platform_status.c.payload).where(*where)).scalar()
        if changes is None:return old or {}
        payload={**(old or {}),**changes}
        if old is None:conn.execute(insert(platform_status).values(tenant_id=tenant_id,plat_id=plat_id,payload=payload))
        else:conn.execute(update(platform_status).where(*where).values(payload=payload))
        return payload


def get_device(tenant_id,plat_id,device_id):
    with get_runtime().engine.connect() as conn:
        payload=conn.execute(select(devices.c.payload).where(devices.c.tenant_id==tenant_id,
            devices.c.plat_id==plat_id,devices.c.device_id==device_id)).scalar()
    return None if payload is None else {**payload,'plat_id':plat_id,'device_id':device_id,'tenant_id':tenant_id}


def update_device(tenant_id,plat_id,device_id,changes,defer_classification=False):
    with get_runtime().lock,get_runtime().engine.begin() as conn:
        where=(devices.c.tenant_id==tenant_id,devices.c.plat_id==plat_id,devices.c.device_id==device_id)
        old=conn.execute(select(devices.c.payload).where(*where).with_for_update()).scalar()
        payload={**(old or {}),**changes}
        if ('device_name' in changes or 'representative_blob' in changes) and 'scene_status' not in changes and payload.get('scene_source')!='manual':
            from integrations.smarteye.runtime import normalized_name,device_scene_input
            from services.image_ingest import sync_config
            if old is None or old.get('scene_status')=='unclassified' or normalized_name(old.get('device_name'))!=normalized_name(payload.get('device_name')) or old.get('representative_blob')!=payload.get('representative_blob') or (defer_classification and old.get('scene_status') in ('pending','processing')):
                config,revision=sync_config(conn,tenant_id)
                payload.update(scene_status='pending' if config.classification_enabled and not defer_classification else 'unclassified',
                    scene_labels=[] if normalized_name((old or {}).get('device_name'))!=normalized_name(payload.get('device_name')) else payload.get('scene_labels',[]),scene_error=None,scene_input=device_scene_input(payload,revision),
                    scene_revision=(old or {}).get('scene_revision',0)+1)
                owner=actor.get() or (old or {}).get('scene_owner')
                if owner:payload['scene_owner']={'tenant_id':tenant_id,'user_id':owner['user_id'],'role':'admin'}
        if old is None:conn.execute(insert(devices).values(tenant_id=tenant_id,plat_id=plat_id,device_id=device_id,payload=payload))
        else:conn.execute(update(devices).where(*where).values(payload=payload))
    return {**payload,'plat_id':plat_id,'device_id':device_id,'tenant_id':tenant_id}


def _public_platform(platform):
    status=_status(actor.get()['tenant_id'],platform['plat_id'])
    with get_runtime().engine.connect() as conn:
        state=devices.c.payload['scene_status'].as_string()
        matched=has_scene_labels(devices.c.payload['scene_labels'],conn.dialect.name)
        counts=conn.execute(select(func.count(),func.sum(case(((state=='completed')&matched,1),else_=0)),
            func.sum(case((state=='failed',1),else_=0)),func.sum(case((state.in_(['pending','processing']),1),else_=0)),
            func.sum(case(((state=='completed')&~matched,1),else_=0))).select_from(devices).where(
            devices.c.tenant_id==actor.get()['tenant_id'],devices.c.plat_id==platform['plat_id'])).one()
    return {**{key:platform.get(key,'') for key in PLATFORM_FIELDS},'has_password':bool(platform.get('password')),
        'connection_status':status.get('connection_status','unchecked'),
        'connection_error':status.get('connection_error'),'checked_at':status.get('checked_at'),'device_count':counts[0],
        'classified_count':counts[1] or 0,'scene_failed_count':counts[2] or 0,'scene_pending_count':counts[3] or 0,'scene_unmatched_count':counts[4] or 0}


def _validate_platform(data,old=None):
    if not isinstance(data,dict):raise ValueError('平台参数必须为对象')
    value={**(old or {}),**{key:data[key] for key in (*PLATFORM_FIELDS,'password') if key in data}}
    for key in ('plat_id','platform_name'):
        if not isinstance(value.get(key),str) or not value[key].strip() or len(value[key])>(160 if key=='plat_id' else 200):
            raise ValueError('平台ID须为1至160字符，名称须为1至200字符')
        value[key]=value[key].strip()
    url=value.get('base_url','')
    if not isinstance(url,str):raise ValueError('平台地址无效')
    parsed=urlsplit(url)
    if url and (parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.fragment or parsed.query):raise ValueError('请输入有效HTTP或HTTPS平台地址')
    try:parsed.port
    except ValueError:raise ValueError('平台端口无效')
    value['base_url']=url.rstrip('/')
    value.setdefault('login_url','/api/oauth/anyTenant/login')
    if not isinstance(value['login_url'],str) or not value['login_url'].startswith('/') or value['login_url'].startswith('//'):
        raise ValueError('登录路径必须以单个/开头')
    for key in ('username','password'):
        if not isinstance(value.get(key,''),str):raise ValueError('登录账号和密码格式无效')
        value.setdefault(key,'')
    if old and not data.get('password'):value['password']=old.get('password','')
    value.setdefault('state','1')
    if value['state'] not in ('0','1'):raise ValueError('平台状态无效')
    return value


def _body():
    data=request.get_json(silent=True)
    if data is None:return {}
    if not isinstance(data,dict):raise ValueError('请求参数必须为对象')
    return data


def _cancelled():
    current=execution.get()
    if not current:return False
    job=get_runtime().get(current[0])
    return not job or job['status'] in ('cancelled','interrupted') or job['attempt']!=current[1]


class _SyncCancelled(Exception):pass


def check_platform_connection(platform):
    if not platform.get('base_url') or not platform.get('username') or not platform.get('password'):
        changes={'connection_status':'unconfigured','connection_error':'请补齐平台地址、账号和密码'}
    else:
        # shortcut: finish the current login flow before stopping; add cancellable retries if faster stopping is needed.
        try:result=login_platform(platform)
        except Exception:result={'success':False,'error_kind':'response'}
        changes={'connection_status':'online' if result.get('success') else 'error',
            'connection_error':None if result.get('success') else ERRORS.get(result.get('error_kind'),'登录失败，请检查连接或账号配置')}
    with get_runtime().lock:
        if _cancelled():return None
        changes['checked_at']=now()
        _status(actor.get()['tenant_id'],platform['plat_id'],changes)
    return {'plat_id':platform['plat_id'],'platform_name':platform['platform_name'],
        'status':'completed' if changes['connection_status']=='online' else 'failed','error':changes['connection_error'],**changes}


def latest_platform_check(tenant_id):
    with get_runtime().engine.connect() as conn:
        task_id=conn.scalar(select(jobs.c.task_id).where(jobs.c.tenant_id==tenant_id,jobs.c.kind=='platform-check')
            .order_by(jobs.c.payload['created_at'].as_string().desc()).limit(1))
    return get_runtime().get(task_id) if task_id else None


def run_platform_checks(task_id,platform_ids):
    attempt=execution.get()[1];results=[]
    targets=[p for p in list_platforms_internal() if p['plat_id'] in platform_ids]
    def check(platform):
        while not _connection_slots.acquire(timeout=.2):
            if _cancelled():return None
        try:
            if _cancelled():return None
            return check_platform_connection(platform)
        finally:_connection_slots.release()
    from concurrent.futures import as_completed
    with ContextExecutor(max_workers=2) as pool:
        for future in as_completed([pool.submit(check,platform) for platform in targets]):
            result=future.result()
            if result is None or _cancelled():continue
            results.append(result)
            online=sum(row['connection_status']=='online' for row in results)
            errors=sum(row['connection_status']=='error' for row in results)
            unconfigured=sum(row['connection_status']=='unconfigured' for row in results)
            get_runtime().patch(task_id,{'platform_results':results,'processed_count':len(results),
                'online_count':online,'error_count':errors,'unconfigured_count':unconfigured,
                'progress':int(len(results)*100/max(1,len(targets))),
                'message':f'已检测 {len(results)}/{len(targets)} 个平台'},attempt)
    get_runtime().patch(task_id,{'status':'completed','progress':100,'message':'全部平台检测完成'},attempt)


def _platform_in_use(platform):
    from integrations.smarteye.device_capture import platform_in_use
    from integrations.smarteye.api.ledger_image_downloader import get_all_schedulers
    from integrations.smarteye.api.ledger_watch_downloader import get_all_watch_states
    def references(data,default_all=False):
        if not isinstance(data,dict):return False
        for key in ('platform_ids','platName','platNames'):
            if key not in data:continue
            values=data[key]
            if not values:return True
            if isinstance(values,str):values=[values]
            if platform['plat_id'] in values or platform['platform_name'] in values:return True
        return default_all
    if platform_in_use(platform['plat_id']):return True
    if any((s.get('enabled') or s.get('is_running')) and references(s,not s.get('platform_ids')) for s in get_all_schedulers()):return True
    if any((s.get('enabled') or s.get('is_running')) and references(s,not s.get('platform_ids')) for s in get_all_watch_states()):return True
    for job in get_runtime().list(tenant_id=actor.get()['tenant_id']):
        if job['status'] not in ('pending','running','processing','cancelling'):continue
        if any(references(source) for source in (job,job.get('request'),job.get('params'))):return True
    return False


def fetch_platform_devices(platform,token=None):
    from integrations.smarteye.api.ledger_image_downloader import _post_with_token,_TOKEN_EXPIRED
    token=token if token is not None else login_platform(platform)
    if not token.get('success'):raise ValueError(ERRORS.get(token.get('error_kind'),'平台登录失败，请检查连接或账号配置'))
    result=[];current=1;expected_total=None;seen=set()
    while True:
        if _cancelled():raise _SyncCancelled()
        payload={'current':current,'size':100,'extra':{},'model':{'alarmTypeId':'','scene':None,
            'deviceId':'','deviceName':None,'areaCode':None,'lastSnapTime':None,'alarmNumStatus':False,
            'startTime':None,'endTime':None,'captureType':None,'activeStatus':1,'orderByTime':True},
            'order':'descending','searchCount':True,'sort':'createdTime','total':0}
        response=_post_with_token(platform['base_url'],'/api/alarm/device/device/config/page',payload,token)
        if response is _TOKEN_EXPIRED or not response or str(response.get('code'))!='0':
            raise ValueError('设备查询失败，请检查平台连接或账号权限')
        data=response.get('data',{})
        if not isinstance(data,dict):raise ValueError('设备分页响应格式无效')
        records=data.get('records') or [];total=int(data.get('total',0))
        if not isinstance(records,list) or total<0:raise ValueError('设备分页响应格式无效')
        if expected_total is None:expected_total=total
        if total!=expected_total:raise ValueError('设备总数在同步期间变化，请重新同步')
        if not records and len(result)<total:raise ValueError('设备分页提前结束，请重新同步')
        for row in records:
            if not isinstance(row,dict) or not row.get('deviceId'):raise ValueError('设备分页缺少有效设备ID')
            device_id=str(row['deviceId'])
            if device_id in seen:raise ValueError('设备分页返回重复设备，请重新同步')
            seen.add(device_id)
            result.append({'device_id':device_id,'device_name':str(row.get('deviceName') or ''),
                'alarm_type_names':row.get('alarmTypeNames') or [],'upstream_active_status':row.get('activeStatus'),
                'upstream_scene':row.get('scene'),'upstream_scenes':row.get('scenes'),'upstream_status':row.get('status')})
        if len(result)>total:raise ValueError('设备分页数量不一致，请重新同步')
        if len(result)==total:break
        current+=1
    return result


def run_device_sync(task_id,platform_ids=None):
    from integrations.smarteye.device_representatives import ensure_representative
    owner=actor.get();attempt=execution.get()[1];results=[]
    platforms=[p for p in list_platforms_internal() if p.get('state')=='1' and (not platform_ids or p['plat_id'] in platform_ids)]
    def sync(platform):
        result={'plat_id':platform['plat_id'],'platform_name':platform['platform_name'],'status':'running','count':0,
            'image_count':0,'image_reused_count':0,'image_missing_count':0,'image_failed_count':0,'device_results':[]}
        with get_runtime().lock:results.append(result)
        try:
            while not _sync_slots.acquire(timeout=0.2):
                if _cancelled():return None
            try:
                if _cancelled():return None
                token=login_platform(platform)
                if not token.get('success'):raise ValueError('平台登录失败')
                rows=fetch_platform_devices(platform,token)
                result['device_total']=len(rows)
                for row in rows:
                    with get_runtime().lock:
                        if _cancelled():return None
                        registered=update_device(owner['tenant_id'],platform['plat_id'],row['device_id'],{
                            **row,'platform_name':platform['platform_name'],'updated_at':now(),
                            'representative_status':'fetching','representative_error':None,'representative_task_id':task_id},defer_classification=True)
                    image,reused=ensure_representative(platform,token,registered)
                    with get_runtime().lock:
                        if _cancelled():return None
                        update_device(owner['tenant_id'],platform['plat_id'],row['device_id'],{**image,'device_name':row['device_name']})
                        result['count']+=1
                        result[{'ready':'image_count','missing':'image_missing_count','failed':'image_failed_count'}[image['representative_status']]]+=1
                        result['image_reused_count']+=int(reused)
                        if image['representative_status']=='failed' and len(result['device_results'])<100:
                            result['device_results'].append({'plat_id':platform['plat_id'],'device_id':row['device_id'],
                                'device_name':row['device_name'],'status':'failed','error':image['representative_error']})
                        get_runtime().patch(task_id,{'platform_results':results,
                            'message':f"{platform['platform_name']}：设备及代表图 {result['count']}/{len(rows)} 台；有图 {result['image_count']}、无图 {result['image_missing_count']}、取图失败 {result['image_failed_count']}"},attempt)
            finally:_sync_slots.release()
            _status(owner['tenant_id'],platform['plat_id'],{'connection_status':'online','connection_error':None,'checked_at':now()})
            with get_runtime().lock:result['status']='completed'
        except _SyncCancelled:return None
        except Exception:
            message='设备同步失败，请检查平台连接、登录配置或设备查询权限'
            _status(owner['tenant_id'],platform['plat_id'],{'connection_status':'error','connection_error':message,'checked_at':now()})
            with get_runtime().lock:result.update(status='failed',error=message)
    from concurrent.futures import as_completed
    with ContextExecutor(max_workers=2) as pool:
        for future in as_completed([pool.submit(sync,platform) for platform in platforms]):
            future.result()
            with get_runtime().lock:get_runtime().patch(task_id,{'platform_results':results,'progress':int(sum(row['status'] in ('completed','failed') for row in results)/len(platforms)*100)},attempt)
    failed=not platforms or any(row['status']=='failed' for row in results)
    get_runtime().patch(task_id,{'status':'failed' if failed else 'completed','progress':100,'platform_results':results,
        'message':'设备同步存在失败，请查看各平台结果' if failed else '设备同步完成'},attempt)


def register_routes(app):
    def guarded(admin=False):
        def decorate(fn):
            @wraps(fn)
            def call(*args,**kwargs):
                if not actor.get():return jsonify(code=401,message='请登录'),401
                if admin and actor.get().get('role')!='admin':return jsonify(code=403,message='仅管理员可管理平台和同步设备'),403
                try:return fn(*args,**kwargs)
                except ValueError as exc:return jsonify(code=400,message=str(exc)),400
                except HTTPException:raise
                except Exception:return jsonify(code=500,message='操作失败，请检查平台配置或服务状态'),500
            return call
        return decorate

    @app.get('/managed-platforms')
    @guarded()
    def managed_platform_list():return jsonify(code=0,data=[_public_platform(p) for p in list_platforms_internal()])

    @app.route('/managed-platforms/check-all',methods=['GET','POST'])
    @guarded(True)
    def managed_platform_check_all():
        if request.method=='GET':return jsonify(code=0,data=latest_platform_check(actor.get()['tenant_id']))
        if not _check_batch_slots.acquire(blocking=False):return jsonify(code=429,message='平台检测仍在执行或停止中，请等待当前登录流程结束'),429
        task_id=None
        try:
            ids=[p['plat_id'] for p in list_platforms_internal()]
            if not ids:raise ValueError('没有可检测的平台')
            task_id='platform_check_'+uuid.uuid4().hex
            get_runtime().create(task_id,'platform-check',{'status':'pending','platform_ids':ids,'total_count':len(ids),
                'processed_count':0,'online_count':0,'error_count':0,'unconfigured_count':0,'progress':0},actor.get())
            future=executor.submit(tracked_run,run_platform_checks,task_id,ids)
            future.add_done_callback(lambda done:_check_batch_slots.release())
        except Exception:
            _check_batch_slots.release()
            if task_id:get_runtime().patch(task_id,{'status':'failed','message':'平台检测任务未能启动，请重试'},1)
            raise
        return jsonify(code=0,data=get_runtime().get(task_id))

    @app.post('/managed-platforms')
    @guarded(True)
    def managed_platform_create():
        value=_validate_platform(_body())
        if get_platform(value['plat_id']):raise ValueError('平台ID已存在')
        write_platform_config(value,create=True)
        return jsonify(code=0,data=_public_platform(value))

    @app.put('/managed-platforms/<plat_id>')
    @guarded(True)
    def managed_platform_edit(plat_id):
        old=get_platform(plat_id)
        if old is None:return jsonify(code=404,message='平台不存在'),404
        check=latest_platform_check(actor.get()['tenant_id'])
        if check and check['status'] in ('pending','running','processing'):raise ValueError('平台检测进行中，请先停止检测再修改配置')
        value=_validate_platform({**_body(),'plat_id':plat_id},old)
        write_platform_config(value)
        _status(actor.get()['tenant_id'],plat_id,{'connection_status':'unchecked','connection_error':None,'checked_at':None})
        return jsonify(code=0,data=_public_platform(value))

    @app.delete('/managed-platforms/<plat_id>')
    @guarded(True)
    def managed_platform_delete(plat_id):
        old=get_platform(plat_id)
        if old is None:return jsonify(code=404,message='平台不存在'),404
        if _platform_in_use(old):raise ValueError('平台仍有启用调度器或运行任务，请先停止任务')
        write_platform_config({**old,'state':'deleted'})
        return jsonify(code=0,message='平台已删除，历史任务与图片保留')

    @app.post('/managed-platforms/<plat_id>/check')
    @guarded(True)
    def managed_platform_check(plat_id):
        platform=get_platform(plat_id)
        if platform is None:return jsonify(code=404,message='平台不存在'),404
        if not _connection_slots.acquire(blocking=False):return jsonify(code=429,message='平台连接检测繁忙，请稍后重试'),429
        try:check_platform_connection(platform)
        finally:_connection_slots.release()
        return jsonify(code=0,data=_public_platform(platform))

    @app.get('/devices')
    @guarded()
    def managed_device_list():
        page=int(request.args.get('page',1));size=int(request.args.get('page_size',50))
        if page<1 or not 1<=size<=200:raise ValueError('分页参数无效')
        scene_match=request.args.get('scene_match','any')
        if scene_match not in ('any','all'):raise ValueError('场景匹配方式无效')
        query=select(devices).where(devices.c.tenant_id==actor.get()['tenant_id'],
            devices.c.plat_id.in_([p['plat_id'] for p in list_platforms_internal()]))
        if request.args.get('platform_id'):query=query.where(devices.c.plat_id==request.args['platform_id'])
        if request.args.get('q'):
            term='%'+request.args['q'].replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%'
            query=query.where(or_(devices.c.device_id.ilike(term,escape='\\'),
                devices.c.payload['device_name'].as_string().ilike(term,escape='\\'),
                devices.c.payload['platform_name'].as_string().ilike(term,escape='\\')))
        if request.args.get('has_image'):
            if request.args['has_image'] not in ('yes','no'):return jsonify(code=400,message='图片筛选参数无效'),400
            has_image=func.coalesce(devices.c.payload['representative_blob'].as_string(),'')!=''
            query=query.where(has_image if request.args['has_image']=='yes' else ~has_image)
        with get_runtime().engine.connect() as conn:
            status=request.args.get('scene_status')
            if status:
                query=query.where(devices.c.payload['scene_status'].as_string()==('completed' if status=='unmatched' else status))
                if status in ('completed','unmatched'):
                    matched=has_scene_labels(devices.c.payload['scene_labels'],conn.dialect.name)
                    query=query.where(~matched if status=='unmatched' else matched)
            selected_scenes=[value for value in request.args.getlist('scene') if value]
            camera_positions=[value for value in request.args.getlist('camera_position') if value]
            if camera_positions:
                from services.image_ingest import sync_config
                from services.scene_categories import scene_filter_values,scene_labels_condition
                config,_=sync_config(conn,actor.get()['tenant_id'])
                values=list(dict.fromkeys(label for position in camera_positions for label in scene_filter_values(position,[config])))
                query=query.where(scene_labels_condition(devices.c.payload['scene_labels'],values,conn.dialect.name))
            if selected_scenes:
                from services.image_ingest import sync_config
                from services.scene_categories import scene_filter_values,scene_labels_condition
                config,_=sync_config(conn,actor.get()['tenant_id'])
                groups=[scene_filter_values(scene,[config]) for scene in dict.fromkeys(selected_scenes)]
                if scene_match=='all':
                    for values in groups:query=query.where(scene_labels_condition(devices.c.payload['scene_labels'],values,conn.dialect.name))
                else:
                    values=list(dict.fromkeys(label for group in groups for label in group))
                    query=query.where(scene_labels_condition(devices.c.payload['scene_labels'],values,conn.dialect.name))
            total=conn.scalar(select(func.count()).select_from(query.subquery()))
            rows=conn.execute(query.order_by(devices.c.plat_id,devices.c.device_id).offset((page-1)*size).limit(size)).mappings().all()
        items=[]
        for row in rows:
            payload=row['payload']
            item={key:payload.get(key) for key in ('device_name','platform_name','scene_error','scene_source','scene_basis','representative_url','representative_status','representative_error','updated_at')}
            item['has_image']=bool(payload.get('representative_blob'))
            items.append({**item,'plat_id':row['plat_id'],'device_id':row['device_id'],
                'scene_labels':payload.get('scene_labels',[]),'scene_status':scene_result_status(payload.get('scene_status','unclassified'),payload.get('scene_labels'))})
        return jsonify(code=0,data={'items':items,'total':total})

    @app.post('/devices/sync')
    @guarded(True)
    def managed_device_sync():
        data=_body();ids=data.get('platform_ids')
        if ids is not None and (not isinstance(ids,list) or any(not isinstance(p,str) for p in ids)):raise ValueError('平台ID列表无效')
        available={p['plat_id'] for p in list_platforms_internal() if p.get('state')=='1'}
        if ids and any(p not in available for p in ids):raise ValueError('所选平台不存在或未启用')
        if not available:raise ValueError('没有启用的平台')
        if not _batch_slots.acquire(blocking=False):return jsonify(code=429,message='已有两个设备同步任务，请稍后重试'),429
        task_id='device_sync_'+uuid.uuid4().hex
        try:
            get_runtime().create(task_id,'device-sync',{'status':'pending','platform_ids':ids or [],'request':data},actor.get())
            future=executor.submit(tracked_run,run_device_sync,task_id,ids)
            future.add_done_callback(lambda done:_batch_slots.release())
        except Exception:
            _batch_slots.release()
            raise
        return jsonify(code=0,data={'task_id':task_id})
