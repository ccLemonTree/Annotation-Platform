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

from PIL import Image
from sqlalchemy import (MetaData, Table, Column, String, Integer, JSON, select, update,
                        insert, UniqueConstraint)

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
            if owner.get('min_long_edge') is not None:payload['min_long_edge']=owner['min_long_edge']
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
                payload={**row['payload'], 'status': 'cancelled', 'cancelled': True, 'message': '任务已取消'}))

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

    def save_image(self, task_id, target, content, attempt, associate=True, collection_name=None):
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
        with self.lock, self.engine.begin() as conn:
            row = conn.execute(select(jobs).where(jobs.c.task_id == task_id).with_for_update()).mappings().one()
            if row['status'] in TERMINAL or row['attempt'] != attempt:
                raise RuntimeError('任务已取消、中断或被重试')
            if associate:
                from services.image_ingest import sync_collection
                from schemas.image_ingest import ImageIngestConfig
                name=collection_name if collection_name is not None else row['payload'].get('platform_name')
                name=name or {'ai-image':'AI生图','material':'SmartEye素材'}.get(row['kind'],'SmartEye（未提供平台）')
                if not isinstance(name,str) or not name.strip() or len(name.strip())>200:raise ValueError('图片集名称无效')
                selected=row['payload'].get('collection_id')
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
            source_id = old or str(uuid.uuid4())
            if not old:
                conn.execute(insert(files).values(id=source_id, task_id=task_id, relative_path=relative,
                    blob_path=key, content_hash=digest, metadata=info))
            else:
                conn.execute(update(files).where(files.c.id == source_id).values(
                    blob_path=key, content_hash=digest, metadata=info))
            if associate:
                from models import LibraryImage, ImageCollectionMember
                from sqlalchemy.dialects.postgresql import insert as pg_insert
                from sqlalchemy.dialects.sqlite import insert as sqlite_insert
                table = LibraryImage.__table__
                create = pg_insert if conn.dialect.name == 'postgresql' else sqlite_insert
                conn.execute(create(table).values(id=uuid.uuid4(), tenant_id=row['tenant_id'],
                    blob_path=key, content_hash=digest, source_type='smarteye',
                    source_record_id=uuid.UUID(source_id), batch_id=task_id, **info).on_conflict_do_nothing(
                        index_elements=['tenant_id','content_hash']))
                image_id = conn.execute(select(table.c.id).where(table.c.tenant_id == row['tenant_id'],
                    table.c.content_hash == digest).with_for_update()).scalar_one()
                if config.classification_enabled:
                    conn.execute(update(table).where(table.c.id==image_id,table.c.scene_status=='unclassified').values(
                        scene_status='pending',scene_config={'collection_id':str(group_id),'revision':policy['revision'],'config':config.model_dump()}))
                members=ImageCollectionMember.__table__
                conn.execute(create(members).values(collection_id=group_id,image_id=image_id)
                    .on_conflict_do_nothing(index_elements=['collection_id','image_id']))
                conn.execute(update(files).where(files.c.id==source_id).values(metadata={**info,'collection_name':name}))
                if row['project_id']:
                    self._associate(conn, row, source_id, key, digest, info, image_id)
        return key

    def _associate(self, conn, job, source_id, key, digest, info, image_id):
        from models import Asset, Project
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        project_id = uuid.UUID(job['project_id'])
        project = conn.execute(select(Project.__table__.c.id).where(Project.__table__.c.id == project_id,
            Project.__table__.c.tenant_id == job['tenant_id']).with_for_update()).first()
        if not project:
            raise ValueError('标注项目不存在')
        stmt = pg_insert(Asset.__table__).values(id=uuid.uuid4(), project_id=project_id, image_id=image_id, blob_path=key,
            content_hash=digest, source_type='smarteye', source_record_id=uuid.UUID(source_id),
            annotation_status='pending', **info).on_conflict_do_nothing(
                constraint='uq_asset_project_content').returning(Asset.__table__.c.id)
        if conn.execute(stmt).scalar():
            conn.execute(update(Project.__table__).where(Project.__table__.c.id == project_id).values(
                total_assets=Project.__table__.c.total_assets + 1))

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

def save_image_content(path, content, associate=True, collection_name=None):
    current = execution.get()
    if not current:
        owner = actor.get()
        if not owner:
            raise RuntimeError('图片保存缺少任务上下文')
        task_id = owner.get('session_id') or str(uuid.uuid4())
        get_runtime().create(task_id, 'material', {'status': 'completed'}, owner)
        current = (task_id, 1)
    return get_runtime().save_image(current[0], path, content, current[1], associate=associate, collection_name=collection_name)

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

def cached_image(path):
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
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            os.link(runtime.root / key, target)
        return True
    return False
