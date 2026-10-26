"""Shared policy for uploads and synchronous SmartEye image writers."""
from sqlalchemy import select
from models.image_ingest import ImageIngestSettings
from schemas.image_ingest import ImageIngestConfig
from config.settings import settings
from models.image_collection import ImageCollection
from fastapi import HTTPException

class ResolutionFiltered(ValueError):
    pass

def check_resolution(width,height,minimum):
    if max(width,height)<minimum:
        raise ResolutionFiltered(f'图片长边必须大于等于{minimum}像素，当前为{width}×{height}')

def default_config():
    return ImageIngestConfig(base_url=settings.QWEN_API_URL)

async def get_config(db,tenant_id,collection_id=None):
    if collection_id is not None:
        group=await db.scalar(select(ImageCollection).where(ImageCollection.id==collection_id,ImageCollection.tenant_id==tenant_id))
        if group is None:raise HTTPException(404,'图片集不存在')
        if group.ingest_config is not None:
            return ImageIngestConfig.model_validate(group.ingest_config),group.ingest_revision
        config,_=await get_config(db,tenant_id)
        return config,group.ingest_revision
    row=await db.get(ImageIngestSettings,tenant_id)
    return (ImageIngestConfig.model_validate(row.config),row.revision) if row else (default_config(),0)

def sync_config(conn,tenant_id):
    row=conn.execute(select(ImageIngestSettings.__table__).where(ImageIngestSettings.tenant_id==tenant_id)).mappings().first()
    return (ImageIngestConfig.model_validate(row['config']),row['revision']) if row else (default_config(),0)

def sync_collection(conn,tenant_id,name=None,collection_id=None):
    table=ImageCollection.__table__
    if collection_id:
        row=conn.execute(select(table).where(table.c.id==collection_id,table.c.tenant_id==tenant_id)).mappings().first()
        if row is None:raise ValueError('图片集不存在')
    else:
        from uuid import uuid4
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert
        config,_=sync_config(conn,tenant_id)
        create=pg_insert if conn.dialect.name=='postgresql' else sqlite_insert
        conn.execute(create(table).values(id=uuid4(),tenant_id=tenant_id,name=name,ingest_config=config.model_dump(),ingest_revision=1)
            .on_conflict_do_nothing(index_elements=['tenant_id','name']))
        row=conn.execute(select(table).where(table.c.tenant_id==tenant_id,table.c.name==name)).mappings().one()
    config=ImageIngestConfig.model_validate(row['ingest_config']) if row['ingest_config'] else sync_config(conn,tenant_id)[0]
    return row,config
