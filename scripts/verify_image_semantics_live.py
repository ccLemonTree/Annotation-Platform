"""Read-only business DB + three inference calls; never persists vectors or annotations."""
import asyncio
import json
import sys
from pathlib import Path
import httpx
from sqlalchemy import select,text
from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
from sqlalchemy.pool import NullPool

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'/'backend'))
from config.settings import settings
from models.image import LibraryImage
from models.model_service import ModelService
from schemas.model_service import ModelServiceConfig
from services.image_semantics import caption,embedding
from services.storage import get_storage


async def main():
    if settings.STORAGE_TYPE=='local' and not Path(settings.STORAGE_LOCAL_PATH).is_absolute():
        settings.STORAGE_LOCAL_PATH=str(Path(__file__).resolve().parents[1]/'backend'/'backend'/settings.STORAGE_LOCAL_PATH)
    engine=create_async_engine(settings.DATABASE_URL,poolclass=NullPool)
    try:
        async with async_sessionmaker(engine)() as db:
            await db.execute(text('SET TRANSACTION READ ONLY'))
            caption_model=await db.scalar(select(ModelService).where(ModelService.enabled.is_(True),ModelService.kind=='caption',ModelService.protocol=='openai_chat').order_by(ModelService.id).limit(1))
            if caption_model is None:raise RuntimeError('no enabled platform chat caption model')
            embedding_model=await db.scalar(select(ModelService).where(ModelService.tenant_id==caption_model.tenant_id,ModelService.enabled.is_(True),ModelService.kind=='embedding').order_by(ModelService.id).limit(1))
            image=await db.scalar(select(LibraryImage).where(LibraryImage.tenant_id==caption_model.tenant_id).order_by(LibraryImage.created_at.desc()).limit(1))
            if embedding_model is None or image is None:raise RuntimeError('platform model/image missing')
            caption_config=ModelServiceConfig.model_validate(caption_model);embedding_config=ModelServiceConfig.model_validate(embedding_model)
            path=image.blob_path
            provenance=dict(caption_model=str(caption_model.id),caption_revision=caption_model.revision,
                embedding_model=str(embedding_model.id),embedding_revision=embedding_model.revision,image_id=str(image.id))
        with await get_storage().get_file(path) as file:content=file.read()
        async with httpx.AsyncClient(follow_redirects=False) as client:
            description=await caption(caption_config,content,'简要描述图片中可见的内容，不要猜测。',client)
            image_vector=await embedding(embedding_config,content=content,client=client)
            text_vector=await embedding(embedding_config,text='图片中的可见场景',client=client)
        print(json.dumps(dict(**provenance,caption_characters=len(description),image_dimensions=len(image_vector),text_dimensions=len(text_vector),
            finite_nonzero_vectors=True,business_writes=0),ensure_ascii=False))
    finally:await engine.dispose()


if __name__=='__main__':asyncio.run(main())
