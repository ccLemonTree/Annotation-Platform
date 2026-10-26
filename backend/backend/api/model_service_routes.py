"""Tenant-scoped inference configuration and one-image previews."""
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from api.deps import get_current_user, get_current_superuser
from api.asset_routes import scoped_asset
from database import get_db_session
from models import ModelService, LibraryImage, User
from schemas.model_service import ModelServiceConfig, ModelServiceResponse, PredictionRequest
from services.model_inference import infer, test_connection
from services.storage import get_storage
from config.settings import settings
from schemas.prompts import ChatPrompts, SCENE_PROMPT

router = APIRouter()


@router.get('/prompt-defaults')
async def prompt_defaults(user: User = Depends(get_current_user)):
    return {**ChatPrompts().model_dump(), 'scene': SCENE_PROMPT}


async def scoped(db, service_id, tenant_id, lock=False):
    query = select(ModelService).where(ModelService.id == service_id, ModelService.tenant_id == tenant_id)
    if lock:
        query = query.with_for_update()
    row = await db.scalar(query)
    if row is None:
        raise HTTPException(404, '模型配置不存在')
    return row


async def commit_config(db):
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, '模型KEY已存在，请使用其他KEY')


@router.get('', response_model=list[ModelServiceResponse])
async def list_models(kind: str | None = None, enabled: bool | None = None,
                      db: AsyncSession = Depends(get_db_session), user: User = Depends(get_current_user)):
    query = select(ModelService).where(ModelService.tenant_id == user.tenant_id)
    if kind is not None:
        query = query.where(ModelService.kind == kind)
    if enabled is not None:
        query = query.where(ModelService.enabled == enabled)
    return (await db.scalars(query.order_by(ModelService.key))).all()


@router.post('', response_model=ModelServiceResponse, status_code=201)
async def create_model(request: ModelServiceConfig, db: AsyncSession = Depends(get_db_session),
                       user: User = Depends(get_current_superuser)):
    row = ModelService(tenant_id=user.tenant_id, **request.model_dump())
    db.add(row); await commit_config(db); await db.refresh(row)
    return row


@router.post('/defaults', response_model=list[ModelServiceResponse])
async def add_defaults(db: AsyncSession = Depends(get_db_session), user: User = Depends(get_current_superuser)):
    known = [('yolov26_fire', 'YOLOv26 Fire', 'detection', '/detect/yolov26_fire'),
             ('yolov11_smoking', 'YOLOv11 Smoking', 'detection', '/detect/yolov11_smoking'),
             ('yolov5_dwcar', 'YOLOv5 DWCar', 'detection', '/detect/yolov5_dwcar'),
             ('rfdetr_fire', 'RF-DETR Fire', 'detection', '/detect/rfdetr_fire'),
             ('rfdetr_fire_sahi', 'RF-DETR Fire SAHI', 'detection', '/detect/rfdetr_fire_sahi'),
             ('sam3', 'SAM3', 'detection', '/detect/sam3'),
             ('fastvlm', 'FastVLM LPR', 'caption', '/caption/fastvlm')]
    configs = [ModelServiceConfig(key=key, name=name, kind=kind, protocol='model_service',
        base_url='http://36.212.5.102:8003', endpoint=endpoint, enabled=key != 'rfdetr_fire_sahi',
        labels=['sam3_person'] if key == 'sam3' else [],
        label_mapping={'sam3_person': 'person'} if key == 'sam3' else
            {'26_fire': 'fire', '26_smoke': 'smoke', '26_hotwork': 'hotwork'} if key == 'yolov26_fire' else {}) for key, name, kind, endpoint in known]
    configs += [ModelServiceConfig(key='qwen', name='Qwen3.8-27B', kind='caption', protocol='openai_chat',
        base_url='http://10.11.12.123:31030/v1', endpoint='/chat/completions', model_name='Qwen3.8-27B',
        api_key_env='QWEN_API_KEY' if settings.QWEN_API_KEY else ''),
        ModelServiceConfig(key='embedding', name='WEMM Embedding 2B', kind='embedding', protocol='openai_embedding',
        base_url='http://36.212.5.102:8002/v1', endpoint='/embeddings', model_name='/models/wemm_embedding_2b',
        api_key_env='EMBEDDING_API_KEY' if settings.EMBEDDING_API_KEY else '')]
    existing = set((await db.scalars(select(ModelService.key).where(ModelService.tenant_id == user.tenant_id))).all())
    for config in configs:
        if config.key not in existing:
            db.add(ModelService(tenant_id=user.tenant_id, **config.model_dump()))
    await commit_config(db)
    return (await db.scalars(select(ModelService).where(ModelService.tenant_id == user.tenant_id).order_by(ModelService.key))).all()


@router.put('/{service_id}', response_model=ModelServiceResponse)
async def update_model(service_id: UUID, request: ModelServiceConfig, db: AsyncSession = Depends(get_db_session),
                       user: User = Depends(get_current_superuser)):
    row = await scoped(db, service_id, user.tenant_id, lock=True)
    for key, value in request.model_dump().items():
        setattr(row, key, value)
    row.revision += 1
    await commit_config(db); await db.refresh(row)
    return row


@router.delete('/{service_id}', status_code=204)
async def delete_model(service_id: UUID, db: AsyncSession = Depends(get_db_session), user: User = Depends(get_current_superuser)):
    await db.delete(await scoped(db, service_id, user.tenant_id, lock=True))
    await db.commit()


@router.post('/{service_id}/test')
async def check_model(service_id: UUID, db: AsyncSession = Depends(get_db_session), user: User = Depends(get_current_superuser)):
    row = await scoped(db, service_id, user.tenant_id)
    config = ModelServiceConfig.model_validate(row)
    await db.rollback()
    return await test_connection(config)


@router.post('/{service_id}/predict')
async def predict(service_id: UUID, request: PredictionRequest, db: AsyncSession = Depends(get_db_session),
                  user: User = Depends(get_current_user)):
    tenant_id = user.tenant_id
    row = await scoped(db, service_id, tenant_id)
    if not row.enabled:
        raise HTTPException(400, '此模型已停用')
    config = ModelServiceConfig.model_validate(row)
    revision = row.revision
    if request.asset_id:
        image = await scoped_asset(db, request.asset_id, user.tenant_id)
    else:
        image = await db.scalar(select(LibraryImage).where(LibraryImage.id == request.image_id, LibraryImage.tenant_id == user.tenant_id))
        if image is None:
            raise HTTPException(404, '图片不存在')
    path, width, height = image.blob_path, image.width, image.height
    await db.rollback()
    if not width or not height:
        raise HTTPException(400, '图片缺少尺寸，无法推理')
    try:
        with await get_storage().get_file(path) as source:
            content = source.read(settings.MAX_UPLOAD_SIZE + 1)
    except (FileNotFoundError, ValueError):
        raise HTTPException(404, '图片文件不存在')
    if len(content) > settings.MAX_UPLOAD_SIZE:
        raise HTTPException(413, '图片超过模型试运行大小限制')
    result = await infer(config, content, width, height, request.prompt)
    current = await scoped(db, service_id, tenant_id)
    if not current.enabled or current.revision != revision:
        raise HTTPException(409, '推理期间模型配置已变化，请使用新配置重试')
    return {**result, 'model_service_id': str(service_id), 'config_revision': revision}
