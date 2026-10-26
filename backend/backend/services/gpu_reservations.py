"""Serialize training holds and offline-validation reservations in one transaction."""
from fastapi import HTTPException
from sqlalchemy import select, text, inspect
from models.training_run import TrainingGpuHold
from models.validation import ValidationRun


async def assert_free(db, devices, exclude_validation=None):
    devices=sorted(set(device for device in devices if device!='cpu'))
    if not devices:return
    connection=await db.connection()
    if connection.dialect.name=='postgresql':
        for device in devices:
            await db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:key))'),{'key':'platform-gpu:'+device})
    if await connection.run_sync(lambda c:inspect(c).has_table('training_gpu_holds')):
        if await db.scalar(select(TrainingGpuHold.gpu_uuid).where(TrainingGpuHold.gpu_uuid.in_(devices)).limit(1)):
            raise HTTPException(409,'所选GPU已被平台训练任务占用')
    if await connection.run_sync(lambda c:inspect(c).has_table('validation_runs')):
        query=select(ValidationRun.id).where(ValidationRun.model_snapshot['source'].as_string()=='checkpoint',
            ValidationRun.model_snapshot['execution']['active'].as_boolean().is_(True),
            ValidationRun.model_snapshot['checkpoint']['device'].as_string().in_(devices))
        if exclude_validation:query=query.where(ValidationRun.id!=exclude_validation)
        if await db.scalar(query.limit(1)):raise HTTPException(409,'所选GPU已被权重验证占用或远端状态待核对')
