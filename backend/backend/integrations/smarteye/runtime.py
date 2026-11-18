"""Durable state and shared originals for the embedded SmartEye workers."""
import contextvars
import hashlib
import json
import os
import threading
import uuid
import warnings
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from datetime import datetime, timezone

from PIL import Image
from sqlalchemy import (MetaData, Table, Column, String, Integer, JSON, select, update,
                        insert, UniqueConstraint, func, and_, or_)

metadata = MetaData()
jobs = Table('smarteye_jobs', metadata,
    Column('task_id', String(160), primary_key=True), Column('kind', String(30), nullable=False),
    Column('tenant_id', Integer, nullable=False), Column('user_id', Integer, nullable=False),
    Column('project_id', String(36)), Column('attempt', Integer, nullable=False, default=1),
    Column('status', String(30), nullable=False), Column('payload', JSON, nullable=False))
files = Table('smarteye_files', metadata,
    Column('id', String(36), primary_key=True), Column('task_id', String(160), nullable=False),
    Column('relative_path', String(500), nullable=False), Column('blob_path', String(500), nullable=False),
    Column('content_hash', String(64), nullable=False), Column('metadata', JSON, nullable=False),
    UniqueConstraint('task_id', 'relative_path', name='uq_smarteye_file'))
schedules = Table('smarteye_schedules', metadata,
    Column('sched_id', String(160), primary_key=True), Column('tenant_id', Integer, nullable=False),
    Column('payload', JSON, nullable=False))

actor = contextvars.ContextVar('smarteye_actor', default=None)
execution = contextvars.ContextVar('smarteye_execution', default=None)
_runtime = None
TERMINAL = ('cancelled', 'interrupted')


def normalized_name(value):
    return ' '.join(str(value or '').split())


def device_scene_input(payload, revision):
    return [normalized_name(payload.get('device_name')), revision] + (['image', payload['representative_blob']] if payload.get('representative_blob') else [])


def device_source(platform_name, record, plat_id=None, device_id=None):
    """Keep upstream device identity before download workers reduce records to URLs."""
    identity = device_id or record.get('deviceId') or record.get('device_id')
    name = normalized_name(record.get('deviceName') or record.get('device_name') or record.get('device'))
    if name == '-':name = ''
    if not identity and (not name or name == '-'):
        return None
    if not plat_id:
        from integrations.smarteye.api.platform_enum import get_combined_platform_mapping
        plat_id = (get_combined_platform_mapping().get(platform_name) or {}).get('platId')
    if not plat_id:
        plat_id = '__platform_name__:' + hashlib.sha256(str(platform_name).encode()).hexdigest()
    source = {'plat_id': str(plat_id), 'device_id': str(identity) if identity else '__name__:' + hashlib.sha256(name.encode()).hexdigest(),
              'device_name': name, 'platform_name': str(platform_name)}
    if not identity or str(plat_id).startswith('__platform_name__:'):source['identity_kind'] = 'name'
    return source


def register_device_source(conn, tenant_id, info, owner, blob_path=None):
    from integrations.smarteye.management_schema import devices
    from services.image_ingest import sync_config
    where = (devices.c.tenant_id == tenant_id, devices.c.plat_id == info['plat_id'], devices.c.device_id == info['device_id'])
    old = conn.execute(select(devices.c.payload).where(*where).with_for_update()).scalar()
    # Device synchronization owns renames; historical capture records may retain old names.
    info['device_name'] = info['device_name'] or (old or {}).get('device_name', '')
    config, revision = sync_config(conn, tenant_id)
    current_name = (old or {}).get('device_name') or info['device_name']
    payload = {**(old or {}), 'device_name': current_name, 'platform_name': info['platform_name']}
    capture_only = owner.get('kind') == 'device-capture'
    if not capture_only and blob_path and not payload.get('representative_blob') and normalized_name(info['device_name']) == normalized_name(current_name):
        from urllib.parse import quote
        payload.update(representative_blob=blob_path, representative_url='/api/smarteye/devices/' + quote(info['plat_id'],safe='') + '/' + quote(info['device_id'],safe='') + '/preview')
    fingerprint = device_scene_input(payload, revision)
    if info.get('identity_kind'):payload['identity_kind'] = info['identity_kind']
    if not capture_only and payload.get('scene_source') != 'manual' and payload.get('scene_input') != fingerprint:
        payload.update(scene_input=fingerprint, scene_revision=payload.get('scene_revision', 0) + 1,
                       scene_labels=[], scene_error=None,
                       scene_status='pending' if config.classification_enabled else 'unclassified',
                       scene_owner={'tenant_id': tenant_id, 'user_id': owner['user_id'], 'role': 'admin'})
    if old is None:
        conn.execute(insert(devices).values(tenant_id=tenant_id, plat_id=info['plat_id'], device_id=info['device_id'], payload=payload))
    else:
        conn.execute(update(devices).where(*where).values(payload=payload))


def inherit_device_scenes(conn, image_id, tenant_id, info=None):
    from models import LibraryImage
    from integrations.smarteye.management_schema import devices
    table = LibraryImage.__table__
    prior = conn.execute(select(table.c.device_info, table.c.scene_labels, table.c.scene_status).where(table.c.id == image_id)).mappings().one()
    previous = prior['device_info'] or {}
    sources = list(previous.get('sources') or ([previous] if previous.get('plat_id') and previous.get('device_id') else []))
    if len(sources) == 1 and 'scene_status' not in sources[0] and prior['scene_status'] == 'completed':
        sources[0] = {**sources[0], 'scene_status': 'completed', 'scene_labels': prior['scene_labels'] or []}
    if info:
        identity = (info['plat_id'], info['device_id'], normalized_name(info['device_name']))
        matching = next((item for item in sources if (item['plat_id'], item['device_id'], normalized_name(item.get('device_name'))) == identity), {})
        sources = [item for item in sources if (item['plat_id'], item['device_id'], normalized_name(item.get('device_name'))) != identity] + [{**matching, **info}]
    if not sources:return
    labels, states, needs_name_confirmation = [], [], False
    for source in sources:
        payload = conn.execute(select(devices.c.payload).where(devices.c.tenant_id == tenant_id,
            devices.c.plat_id == source['plat_id'], devices.c.device_id == source['device_id'])).scalar() or {}
        if normalized_name(source.get('device_name')) == normalized_name(payload.get('device_name')):
            source.update(scene_status=payload.get('scene_status', 'unclassified'), scene_labels=payload.get('scene_labels', []),scene_basis=payload.get('scene_basis','device_name'))
        elif source.get('scene_status') != 'completed':needs_name_confirmation = True
        states.append(source.get('scene_status', 'unclassified'))
        if source.get('scene_status') == 'completed':
            labels.extend(label for label in source.get('scene_labels', []) if label not in labels)
    state = 'completed' if 'completed' in states else 'failed' if states and all(value == 'failed' for value in states) else 'unclassified'
    waiting = 'processing' if 'processing' in states else 'pending' if 'pending' in states else state
    conn.execute(update(table).where(table.c.id == image_id).values(device_info={**sources[0], 'sources': sources},
        scene_labels=labels, scene_status=state, scene_error='设备场景分类失败，请重试设备分类' if state == 'failed' else None,
        scene_config=None, scene_metadata={'basis': 'device_name', 'device_scene_basis':'image' if any(source.get('scene_basis')=='image' for source in sources) else 'device_name', 'device_scene_status': waiting,
            'name_confirmation_required': needs_name_confirmation}))


def propagate_device_scenes(conn, tenant_id, plat_id, device_id):
    from models import LibraryImage
    table = LibraryImage.__table__
    direct = and_(table.c.device_info['plat_id'].as_string() == plat_id, table.c.device_info['device_id'].as_string() == device_id)
    if conn.dialect.name == 'postgresql':
        from sqlalchemy.dialects.postgresql import JSONB
        member = table.c.device_info.cast(JSONB).contains({'sources': [{'plat_id': plat_id, 'device_id': device_id}]})
    else:
        source = func.json_each(table.c.device_info, '$.sources').table_valued('value')
        member = select(1).select_from(source).where(func.json_extract(source.c.value, '$.plat_id') == plat_id,
            func.json_extract(source.c.value, '$.device_id') == device_id).exists()
    for image_id in conn.execute(select(table.c.id).where(table.c.tenant_id == tenant_id, or_(direct, member))).scalars():
        inherit_device_scenes(conn, image_id, tenant_id)

class ContextExecutor(ThreadPoolExecutor):
    def submit(self, fn, /, *args, **kwargs):
        context = contextvars.copy_context()
        if fn is tracked_run:
            kwargs['_expected_attempt'] = get_runtime().get(args[1])['attempt']
        return super().submit(context.run, fn, *args, **kwargs)

class Runtime:
    def __init__(self, engine, storage_root):
        self.engine = engine
        self.root = Path(storage_root).resolve()
        self.lock = threading.RLock()

    def create(self, task_id, kind, payload, owner):
        with self.engine.begin() as conn:
            if conn.execute(select(jobs.c.task_id).where(jobs.c.task_id == task_id)).first():
                return
            payload={**payload}
            if kind in ('device-sync','device-scenes','device-capture','platform-check'):
                payload.update(created_at=datetime.now(timezone.utc).isoformat(),updated_at=datetime.now(timezone.utc).isoformat())
            if owner.get('min_long_edge') is not None:payload['min_long_edge']=owner['min_long_edge']
            if kind == 'device-capture' and owner.get('vector_test'):
                payload['vector_test'] = True
            if owner.get('collection_id'):
                from services.image_ingest import sync_collection
                group,config=sync_collection(conn,owner['tenant_id'],collection_id=uuid.UUID(owner['collection_id']))
                payload.update(collection_id=owner['collection_id'],collection_policies={str(group['id']):{
                    'config':config.model_dump(),'revision':group['ingest_revision']}})
            conn.execute(insert(jobs).values(task_id=task_id, kind=kind, tenant_id=owner['tenant_id'],
                user_id=owner['user_id'], project_id=owner.get('project_id'), attempt=1,
                status=payload.get('status', 'pending'), payload=json_safe(payload)))

    def get(self, task_id):
        with self.engine.connect() as conn:
            row = conn.execute(select(jobs).where(jobs.c.task_id == task_id)).mappings().first()
        if not row:
            return None
        return {**row['payload'], **{k: row[k] for k in ('task_id', 'kind', 'tenant_id', 'user_id',
                                                       'project_id', 'attempt', 'status')}}

    def list(self, tenant_id=None, kind=None):
        query = select(jobs.c.task_id)
        if tenant_id is not None:
            query = query.where(jobs.c.tenant_id == tenant_id)
        if kind:
            query = query.where(jobs.c.kind == kind)
        with self.engine.connect() as conn:
            ids = list(conn.execute(query).scalars())
        return [self.get(task_id) for task_id in ids]

    def patch(self, task_id, changes, attempt):
        with self.lock, self.engine.begin() as conn:
            query = select(jobs).where(jobs.c.task_id == task_id).with_for_update()
            row = conn.execute(query).mappings().first()
            if not row or row['attempt'] != attempt or row['status'] in TERMINAL:
                return False
            payload = {**row['payload'], **json_safe(changes)}
            if 'created_at' in payload:payload['updated_at']=datetime.now(timezone.utc).isoformat()
            if payload.get('cancelled') or payload.get('status') == '已取消':
                payload['status'] = 'cancelled'
            conn.execute(update(jobs).where(jobs.c.task_id == task_id).values(
                status=payload.get('status', row['status']), payload=payload))
            return True

    def cancel(self, task_id):
        with self.lock, self.engine.begin() as conn:
            row = conn.execute(select(jobs).where(jobs.c.task_id == task_id).with_for_update()).mappings().first()
            if not row:
                raise KeyError(task_id)
            conn.execute(update(jobs).where(jobs.c.task_id == task_id).values(status='cancelled',
                payload={**row['payload'], 'status': 'cancelled', 'cancelled': True, 'message': '任务已取消',
                         **({'updated_at':datetime.now(timezone.utc).isoformat()} if 'created_at' in row['payload'] else {})}))
            if row['kind'] == 'device-sync':
                from integrations.smarteye.management_schema import devices
                fetching = conn.execute(select(devices).where(devices.c.tenant_id == row['tenant_id'],
                    devices.c.payload['representative_task_id'].as_string() == task_id,
                    devices.c.payload['representative_status'].as_string() == 'fetching')).mappings().all()
                for device in fetching:
                    conn.execute(update(devices).where(devices.c.tenant_id == device['tenant_id'],
                        devices.c.plat_id == device['plat_id'], devices.c.device_id == device['device_id']).values(
                        payload={**device['payload'], 'representative_status': 'failed',
                                 'representative_error': '同步已停止，代表图未完成；可重新同步'}))

    def recover(self):
        with self.engine.begin() as conn:
            conn.execute(update(jobs).where(jobs.c.status.in_(['pending', 'running', 'processing'])).values(
                status='interrupted'))

    def retry(self, task_id):
        with self.lock, self.engine.begin() as conn:
            row = conn.execute(select(jobs).where(jobs.c.task_id == task_id).with_for_update()).mappings().one()
            if row['status'] not in ('failed', 'cancelled', 'interrupted'):
                raise ValueError('仅失败、取消或中断任务可重试')
            if row['kind'] == 'ai':
                raise ValueError('AI任务不能自动重试，请核对计费与远端任务后重新提交')
            conn.execute(update(jobs).where(jobs.c.task_id == task_id).values(attempt=row['attempt'] + 1,
                status='pending', payload={**row['payload'], 'cancelled': False, 'status': 'pending',
                                          'progress': 0, 'message': '等待重试'}))

    def save_image(self, task_id, target, content, attempt, associate=True, collection_name=None, device_info=None):
        from config.settings import settings
        from api.asset_routes import IMAGE_TYPES
        target = Path(target).resolve()
        if not target.is_relative_to(self.root / 'smarteye'):
            raise ValueError('SmartEye输出必须位于统一存储目录')
        if len(content) > settings.MAX_UPLOAD_SIZE:
            raise ValueError('图片超过大小限制')
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(BytesIO(content)) as img:
                width, height, fmt = img.width, img.height, img.format
                img.verify()
            with Image.open(BytesIO(content)) as img:
                img.load()
        if fmt not in IMAGE_TYPES:
            raise ValueError('不支持的图片格式')
        digest = hashlib.sha256(content).hexdigest()
        key = f'images/{digest[:2]}/{digest}.{IMAGE_TYPES[fmt][0]}'
        canonical = self.root / key
        info = dict(width=width, height=height, format=fmt, file_size=len(content), original_name=target.name)
        source = None
        if device_info:
            if not isinstance(device_info, dict) or any(not isinstance(device_info.get(name), str) or not device_info[name].strip() for name in ('plat_id', 'device_id')):
                raise ValueError('设备来源必须包含平台和设备ID')
            source = {name: normalized_name(device_info.get(name)) for name in ('plat_id','device_id','device_name','platform_name')}
            if device_info.get('identity_kind') == 'name':source['identity_kind'] = 'name'
        with self.lock, self.engine.begin() as conn:
            row = conn.execute(select(jobs).where(jobs.c.task_id == task_id).with_for_update()).mappings().one()
            if row['status'] in TERMINAL or row['attempt'] != attempt:
                raise RuntimeError('任务已取消、中断或被重试')
            if row['kind'] == 'device-capture' and row['payload'].get('vector_test'):
                import asyncio
                from models import Project
                from models.vector_test import VectorTestSample
                from services.storage import LocalStorage
                from services.vector_test_data import original_values
                if max(width, height) < row['payload'].get('min_long_edge', 0):
                    return None
                project_id = uuid.UUID(row['project_id'])
                if conn.scalar(select(Project.id).where(Project.id == project_id,
                    Project.tenant_id == row['tenant_id']).with_for_update()) is None:
                    raise ValueError('检索测试所属项目不存在')
                values = original_values(project_id, digest, IMAGE_TYPES[fmt][0], 'smarteye',
                    device_name=(source or {}).get('device_name'), **info)
                if conn.scalar(select(VectorTestSample.id).where(VectorTestSample.project_id == project_id,
                    VectorTestSample.blob_path == values['blob_path'])) is None:
                    asyncio.run(LocalStorage(str(self.root)).save_file(BytesIO(content), values['blob_path']))
                    conn.execute(insert(VectorTestSample).values(**values))
                return values['blob_path']
            follow=following=None
            if associate and row['project_id'] and row['kind']=='device-capture':
                from models.project_work import ProjectWorkJob
                follow=select(ProjectWorkJob.id).where(ProjectWorkJob.project_id==uuid.UUID(row['project_id']),
                    ProjectWorkJob.follow_uploads.is_(True),ProjectWorkJob.options['workflow_streaming'].as_boolean().is_(True),
                    ProjectWorkJob.options['workflow_image_kind'].as_string().is_distinct_from('crops'))
                # Upload association also locks the workflow before the image and project.
                following=conn.scalar(follow.with_for_update())
            if associate:
                from services.image_ingest import sync_collection
                from schemas.image_ingest import ImageIngestConfig
                name=collection_name if collection_name is not None else row['payload'].get('platform_name')
                name=name or {'ai-image':'AI生图','material':'SmartEye素材'}.get(row['kind'],'SmartEye（未提供平台）')
                if not isinstance(name,str) or not name.strip() or len(name.strip())>200:raise ValueError('图片集名称无效')
                selected=row['payload'].get('collection_id')
                if row['project_id'] and not selected:
                    from models import Project
                    name=conn.scalar(select(Project.project_name).where(Project.id==uuid.UUID(row['project_id']),
                        Project.tenant_id==row['tenant_id']))
                    if not name:raise ValueError('标注项目不存在')
                group,config=sync_collection(conn,row['tenant_id'],name.strip(),uuid.UUID(selected) if selected else None)
                group_id=group['id'];name=group['name']
                policies={**row['payload'].get('collection_policies',{})}
                policy=policies.setdefault(str(group_id),{'config':config.model_dump(),'revision':group['ingest_revision']})
                config=ImageIngestConfig.model_validate(policy['config'])
                row={**row,'payload':{**row['payload'],'collection_policies':policies}}
                conn.execute(update(jobs).where(jobs.c.task_id==task_id).values(payload=row['payload']))
                minimum=row['payload'].get('min_long_edge',config.min_long_edge)
                if max(width,height)<minimum:
                    payload={**row['payload'],'filtered_count':row['payload'].get('filtered_count',0)+1}
                    conn.execute(update(jobs).where(jobs.c.task_id==task_id).values(payload=payload))
                    return None
            canonical.parent.mkdir(parents=True, exist_ok=True)
            if not canonical.exists():
                import asyncio
                from services.storage import LocalStorage
                asyncio.run(LocalStorage(str(self.root)).save_file(BytesIO(content), key))
            target.parent.mkdir(parents=True, exist_ok=True)
            if target != canonical and (not target.exists() or not target.samefile(canonical)):
                temp = target.with_name(f'.{uuid.uuid4().hex}.link')
                try:
                    os.link(canonical, temp)
                    os.replace(temp, target)
                finally:
                    temp.unlink(missing_ok=True)
            relative = target.relative_to(self.root).as_posix()
            old = conn.execute(select(files.c.id).where(files.c.task_id == task_id,
                                                      files.c.relative_path == relative)).scalar()
            previous_metadata = conn.execute(select(files.c.metadata).where(files.c.id == old)).scalar() if old else {}
            history = list((previous_metadata or {}).get('device_sources') or
                           ([(previous_metadata or {})['device_info']] if (previous_metadata or {}).get('device_info') else []))
            if source and source not in history:history.append(source)
            source_metadata = {**({'device_info': source} if source else {}), **({'device_sources': history} if history else {})}
            source_id = old or str(uuid.uuid4())
            if not old:
                conn.execute(insert(files).values(id=source_id, task_id=task_id, relative_path=relative,
                    blob_path=key, content_hash=digest, metadata={**info, **source_metadata}))
            else:
                conn.execute(update(files).where(files.c.id == source_id).values(
                    blob_path=key, content_hash=digest, metadata={**info, **source_metadata}))
            if associate:
                from models import LibraryImage, ImageCollectionMember
                from sqlalchemy.dialects.postgresql import insert as pg_insert
                from sqlalchemy.dialects.sqlite import insert as sqlite_insert
                table = LibraryImage.__table__
                create = pg_insert if conn.dialect.name == 'postgresql' else sqlite_insert
                if source:
                    register_device_source(conn, row['tenant_id'], source, row, key)
                conn.execute(create(table).values(id=uuid.uuid4(), tenant_id=row['tenant_id'],
                    blob_path=key, content_hash=digest, source_type='smarteye',
                    source_record_id=uuid.UUID(source_id), batch_id=task_id, **info).on_conflict_do_nothing(
                        index_elements=['tenant_id','content_hash']))
                image_id = conn.execute(select(table.c.id).where(table.c.tenant_id == row['tenant_id'],
                    table.c.content_hash == digest).with_for_update()).scalar_one()
                if source:
                    inherit_device_scenes(conn, image_id, row['tenant_id'], source)
                elif config.classification_enabled:
                    conn.execute(update(table).where(table.c.id==image_id,table.c.scene_status=='unclassified').values(
                        scene_status='pending',scene_config={'collection_id':str(group_id),'revision':policy['revision'],'config':config.model_dump()}))
                members=ImageCollectionMember.__table__
                conn.execute(create(members).values(collection_id=group_id,image_id=image_id)
                    .on_conflict_do_nothing(index_elements=['collection_id','image_id']))
                conn.execute(update(files).where(files.c.id==source_id).values(metadata={**info,'collection_name':name,
                    **source_metadata}))
                if row['project_id']:
                    self._associate(conn, row, source_id, key, digest, {**info, **({'device_info': source} if source else {})}, image_id, follow, following)
        return key

    def _associate(self, conn, job, source_id, key, digest, info, image_id, follow=None, following=None):
        from models import Asset, Project
        from models.project_work import ProjectWorkItem
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert
        create = pg_insert if conn.dialect.name=='postgresql' else sqlite_insert
        project_id = uuid.UUID(job['project_id'])
        project = conn.execute(select(Project.__table__.c.id).where(Project.__table__.c.id == project_id,
            Project.__table__.c.tenant_id == job['tenant_id']).with_for_update()).first()
        if not project:
            raise ValueError('标注项目不存在')
        stmt = create(Asset.__table__).values(id=uuid.uuid4(), project_id=project_id, image_id=image_id, blob_path=key,
            content_hash=digest, source_type='smarteye', source_record_id=uuid.UUID(source_id),
            annotation_status='pending', **info).on_conflict_do_nothing(
                index_elements=['project_id','content_hash']).returning(Asset.__table__.c.id)
        if asset_id:=conn.scalar(stmt):
            conn.execute(update(Project.__table__).where(Project.__table__.c.id == project_id).values(
                total_assets=Project.__table__.c.total_assets + 1))
            if follow is not None:
                following=following or conn.scalar(follow)
                if following:
                    conn.execute(create(ProjectWorkItem.__table__).values(job_id=following,asset_id=asset_id,content_hash=digest)
                        .on_conflict_do_nothing(index_elements=['job_id','asset_id']))

def json_safe(value):
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))

def get_runtime():
    global _runtime
    if _runtime is None:
        from sqlalchemy import create_engine
        from sqlalchemy.engine import URL
        from config.settings import settings
        if settings.STORAGE_TYPE != 'local':
            raise RuntimeError('SmartEye当前使用本地共享原图；EOS接入后才能切换存储类型')
        url = URL.create('postgresql+psycopg2', username=settings.DB_USER, password=settings.DB_PASSWORD,
                         host=settings.DB_HOST, port=settings.DB_PORT, database=settings.DB_NAME)
        _runtime = Runtime(create_engine(url, pool_pre_ping=True), settings.STORAGE_LOCAL_PATH)
    return _runtime

class TrackedState(dict):
    def __init__(self, task_id, value):
        super().__init__(value)
        self.task_id = task_id
        self.attempt = value['attempt']

    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        get_runtime().patch(self.task_id, {key: value}, self.attempt)

    def update(self, *args, **kwargs):
        changes = dict(*args, **kwargs)
        super().update(changes)
        get_runtime().patch(self.task_id, changes, self.attempt)

    def get(self, key, default=None):
        if key == 'cancelled':
            latest = get_runtime().get(self.task_id)
            return latest['status'] in TERMINAL or latest['attempt'] != self.attempt
        return super().get(key, default)

class DurableTasks(dict):
    def __init__(self, kind):
        super().__init__()
        self.kind = kind

    def __setitem__(self, key, value):
        owner = actor.get()
        if owner is None:
            raise RuntimeError('后台任务缺少操作者')
        value = {**value, 'request': owner.get('request', {})}
        get_runtime().create(key, self.kind, value, owner)
        super().__setitem__(key, TrackedState(key, get_runtime().get(key)))

    def get(self, key, default=None):
        value = get_runtime().get(key)
        if value and value['kind'] == self.kind:
            current = execution.get()
            if current and current[0] == key:
                value['attempt'] = current[1]
            state = TrackedState(key, value)
            super().__setitem__(key, state)
            return state
        return default

    def __getitem__(self, key):
        result = self.get(key)
        if result is None:
            raise KeyError(key)
        return result

    def __contains__(self, key):
        return self.get(key) is not None

    def values(self):
        return [self.get(row['task_id']) for row in get_runtime().list(kind=self.kind)]

    def items(self):
        return [(row['task_id'], self.get(row['task_id'])) for row in get_runtime().list(kind=self.kind)]

def tracked_run(fn, task_id, *args, **kwargs):
    job = get_runtime().get(task_id)
    expected_attempt = kwargs.pop('_expected_attempt', job['attempt'] if job else None)
    if not job:
        raise RuntimeError('任务未登记')
    token = execution.set((task_id, job['attempt']))
    try:
        if job['status'] in TERMINAL or job['attempt'] != expected_attempt:
            return
        get_runtime().patch(task_id, {'status': 'running'}, job['attempt'])
        return fn(task_id, *args, **kwargs)
    except Exception as exc:
        get_runtime().patch(task_id, {'status': 'failed', 'message': str(exc)}, job['attempt'])
        raise
    finally:
        if job['kind'] == 'ai':
            from integrations.smarteye.api.ai_image import get_runtime_task
            state = get_runtime_task(task_id)
            if state:
                get_runtime().patch(task_id, state, job['attempt'])
        execution.reset(token)

def save_image_content(path, content, associate=True, collection_name=None, device_info=None):
    current = execution.get()
    if not current:
        owner = actor.get()
        if not owner:
            raise RuntimeError('图片保存缺少任务上下文')
        task_id = owner.get('session_id') or str(uuid.uuid4())
        get_runtime().create(task_id, 'material', {'status': 'completed'}, owner)
        current = (task_id, 1)
    return get_runtime().save_image(current[0], path, content, current[1], associate=associate, collection_name=collection_name, device_info=device_info)

def response_bytes(response):
    from config.settings import settings
    size = 0
    result = BytesIO()
    for chunk in response.iter_content(65536):
        size += len(chunk)
        if size > settings.MAX_UPLOAD_SIZE:
            raise ValueError('图片超过大小限制')
        result.write(chunk)
    return result.getvalue()

def cached_image(path, device_info=None, associate=True, collection_name=None):
    current = execution.get()
    if not current:
        return False
    runtime = get_runtime()
    job = runtime.get(current[0])
    if not job or job['status'] in TERMINAL or job['attempt'] != current[1]:
        raise RuntimeError('任务已取消、中断或被重试')
    target = Path(path).resolve()
    if not target.is_relative_to(runtime.root / 'smarteye'):
        raise ValueError('非法输出路径')
    with runtime.engine.connect() as conn:
        key = conn.execute(select(files.c.blob_path).where(files.c.task_id == current[0],
            files.c.relative_path == target.relative_to(runtime.root).as_posix())).scalar()
    if key and (runtime.root / key).is_file():
        if device_info:
            return bool(runtime.save_image(current[0], target, (runtime.root / key).read_bytes(), current[1],
                associate=associate, device_info=device_info, collection_name=collection_name))
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            os.link(runtime.root / key, target)
        return True
    return False
