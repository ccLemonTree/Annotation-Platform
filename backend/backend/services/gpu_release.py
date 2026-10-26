"""Administrator-confirmed process/task stops; never drop a live GPU reservation."""
import asyncio,logging
from typing import Literal
from uuid import UUID
from itsdangerous import URLSafeTimedSerializer,BadSignature,SignatureExpired
from pydantic import BaseModel,ConfigDict,Field,model_validator
from sqlalchemy import select,or_
from fastapi import HTTPException
from config.settings import settings
from models.training_run import TrainingRun,TrainingGpuHold
from models.validation import ValidationRun
from services import training_runs,gpu_servers,gpu_resources,validation

logger=logging.getLogger(__name__)


class ReleaseTarget(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    pid:int|None=Field(default=None,gt=1,le=2147483647)
    gpu_uuid:str|None=Field(default=None,pattern=r'^GPU-[A-Za-z0-9-]{1,80}$')
    task_kind:Literal['training','validation']|None=None
    task_id:UUID|None=Field(default=None,strict=False)

    @model_validator(mode='after')
    def one_target(self):
        if self.pid is not None and self.gpu_uuid and self.task_kind is None and self.task_id is None:return self
        if self.pid is None and self.gpu_uuid is None and self.task_kind and self.task_id:return self
        raise ValueError('请选择一个GPU进程或平台任务')


class ReleaseConfirm(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    token:str=Field(min_length=1,max_length=16000)
    force:bool=False


async def access(db,user,server_id):
    if server_id=='default':return dict(id=server_id,name=settings.GPU_SERVER_NAME,revision=0,connection=None)
    try:id=UUID(server_id)
    except ValueError:raise HTTPException(400,'服务器ID格式无效') from None
    server=await gpu_servers.scoped_server(db,id,user.tenant_id)
    if not server.enabled:raise HTTPException(409,'服务器已停用')
    return dict(id=server_id,name=server.name,revision=server.revision,connection=dict(host=server.host,port=server.port,
        username=server.username,password=gpu_servers.decrypt_password(server.password_encrypted)))


async def remote(server,action,**values):
    return await asyncio.to_thread(training_runs.bridge,'platform_training' if server['connection'] else server['name'],
        dict(connection=server['connection'],executor='gpu_release',payload=dict(action=action,**values)))


async def task_target(db,user,server,kind,id):
    if kind=='training':
        run=await training_runs.scoped(db,id,user.tenant_id)
        if run.server['id']!=server['id'] or run.server['name']!=server['name']:raise HTTPException(409,'任务不属于当前服务器连接')
        return run,dict(kind=kind,id=str(run.id),name=run.name,gpu_uuids=run.config['gpu_uuids'])
    run=await db.scalar(select(ValidationRun).where(ValidationRun.id==id,ValidationRun.tenant_id==user.tenant_id).execution_options(populate_existing=True))
    if run is None:raise HTTPException(404,'验证或预览任务不存在')
    snapshot=run.model_snapshot;checkpoint=snapshot.get('checkpoint',{})
    if snapshot.get('source')!='checkpoint' or checkpoint['server']['id']!=server['id'] or checkpoint['server']['name']!=server['name']:
        raise HTTPException(409,'任务不属于当前服务器连接')
    return run,dict(kind=kind,id=str(run.id),name=run.name,gpu_uuids=[checkpoint['device']],execution=snapshot['execution']['id'])


async def tasks(db,user,server_id):
    server=await access(db,user,server_id)
    training=(await db.scalars(select(TrainingRun).where(TrainingRun.tenant_id==user.tenant_id,
        TrainingRun.server['id'].as_string()==server_id,or_(TrainingRun.status.in_(training_runs.ACTIVE),
        TrainingRun.id.in_(select(TrainingGpuHold.run_id)))))).all()
    runs=(await db.scalars(select(ValidationRun).where(ValidationRun.tenant_id==user.tenant_id,
        ValidationRun.model_snapshot['source'].as_string()=='checkpoint',
        ValidationRun.model_snapshot['checkpoint']['server']['id'].as_string()==server_id,
        ValidationRun.model_snapshot['execution']['active'].as_boolean().is_(True)))).all()
    result=[]
    for kind,rows in [('training',training),('validation',runs)]:
        for run in rows:
            _,target=await task_target(db,user,server,kind,run.id)
            if target['gpu_uuids']!=['cpu']:result.append({**target,'status':run.status})
    return result


async def preview(db,user,server_id,request):
    server=await access(db,user,server_id)
    if request.task_kind:
        _,target=await task_target(db,user,server,request.task_kind,request.task_id)
    else:
        target=await remote(server,'inspect',pid=request.pid,gpu_uuid=request.gpu_uuid)
        labels=(target.get('container') or {}).get('labels',{})
        kind='training' if labels.get('platform.training.id') else 'validation' if labels.get('platform.validation.run') else None
        if kind:
            id=labels['platform.training.id' if kind=='training' else 'platform.validation.run']
            _,task=await task_target(db,user,server,kind,UUID(id))
            if kind=='validation' and task['execution']!=labels.get('platform.validation.execution'):raise HTTPException(409,'验证执行轮次已变化')
            target={**task,'process':target}
        else:target={**target,'kind':'process'}
    context=dict(user_id=user.id,tenant_id=user.tenant_id,server_id=server_id,revision=server['revision'],server_name=server['name'])
    token=URLSafeTimedSerializer(settings.JWT_SECRET_KEY,salt='gpu-release-v1').dumps(dict(context=context,target=target))
    return dict(server_name=server['name'],target=target,token=token)


async def confirm(db,user,server_id,request):
    try:payload=URLSafeTimedSerializer(settings.JWT_SECRET_KEY,salt='gpu-release-v1').loads(request.token,max_age=120)
    except (BadSignature,SignatureExpired):raise HTTPException(409,'确认已过期或无效，请重新选择目标') from None
    server=await access(db,user,server_id)
    context=dict(user_id=user.id,tenant_id=user.tenant_id,server_id=server_id,revision=server['revision'],server_name=server['name'])
    if payload['context']!=context:raise HTTPException(409,'操作者或服务器配置已变化，请重新确认')
    target=payload['target']
    logger.info('GPU release requested user=%s tenant=%s server=%s kind=%s id=%s',user.id,user.tenant_id,server_id,target['kind'],target.get('id',target.get('pid')))
    try:
        if target['kind']=='process':
            result=await remote(server,'stop',target={k:v for k,v in target.items() if k!='kind'},force=request.force)
        else:
            run,current=await task_target(db,user,server,target['kind'],UUID(target['id']))
            if {k:v for k,v in target.items() if k!='process'}!=current:raise HTTPException(409,'任务或执行轮次已变化，请重新确认')
            if target['kind']=='training':
                if run.status in training_runs.ACTIVE:await training_runs.stop(db,run)
                else:
                    evidence=await training_runs.remote(db,run,'status')
                    if evidence['status'] not in ('missing','completed','failed','canceled'):raise HTTPException(409,'远端训练仍可能运行，资源预留保留')
                    await training_runs.release(db,run);await db.commit()
            else:
                run=await validation.cancel_run(db,run)
                if run.model_snapshot['execution']['active']:raise HTTPException(409,'远端停止尚未确认，资源预留保留；请重新确认停止')
            result=dict(stopped=True,message='所选任务已停止，平台资源预留已释放；其他进程可能仍占用GPU')
        logger.info('GPU release result user=%s server=%s stopped=%s',user.id,server_id,result['stopped'])
        return result
    finally:
        gpu_resources.invalidate_connection(server_id)
