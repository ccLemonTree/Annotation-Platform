"""Persistent freeze queue and progress reporting."""
import asyncio
import logging
from datetime import timezone
from time import monotonic

from anyio import from_thread
from fastapi import HTTPException
from sqlalchemy import select, update

from models import ExportSnapshotJob, User

ACTIVE = ('queued', 'preparing', 'packing', 'saving')
logger = logging.getLogger(__name__)
_worker = None


def summary(job):
    return dict(id=str(job.id), status=job.status, packed=job.packed, total=job.total,
        error=job.error, version_label=job.request.get('version_label', ''),
        format=job.request.get('dataset_format') or job.request['format'],
        created_at=job.created_at.replace(tzinfo=timezone.utc))


async def enqueue(db, user, project_id, kind, request):
    from api.asset_routes import scoped_project
    await scoped_project(db, project_id, user.tenant_id, for_update=True)
    payload = request.model_dump(mode='json')
    payload['version_label'] = payload['version_label'].strip()
    active = (await db.scalars(select(ExportSnapshotJob).where(
        ExportSnapshotJob.project_id == project_id, ExportSnapshotJob.kind == kind,
        ExportSnapshotJob.status.in_(ACTIVE)))).all()
    for job in active:
        if job.request == payload:
            return summary(job)
    job = ExportSnapshotJob(project_id=project_id, user_id=user.id, kind=kind, request=payload)
    db.add(job)
    await db.commit()
    return summary(job)


async def process_one(sessions):
    from api.export_routes import (
        AnnotationSnapshotRequest, SnapshotRequest, freeze_annotation_package, freeze_detection_package,
    )
    async with sessions() as db:
        job = await db.scalar(select(ExportSnapshotJob).where(ExportSnapshotJob.status == 'queued')
            .order_by(ExportSnapshotJob.created_at, ExportSnapshotJob.id).limit(1).with_for_update(skip_locked=True))
        if job is None:
            return False
        job_id = job.id
        job.status = 'preparing'
        await db.commit()

        async def save_progress(packed, total):
            job.status = 'packing'
            job.packed, job.total = packed, total
            await db.commit()

        last = 0.0

        def progress(packed, total):
            nonlocal last
            now = monotonic()
            if not packed or packed == total or now - last >= 2:
                from_thread.run(save_progress, packed, total)
                last = now

        try:
            user = await db.get(User, job.user_id)
            if not user or not user.is_active or user.role != 'admin':
                raise HTTPException(403, '提交账号已无冻结权限')
            if job.kind == 'detection':
                await freeze_detection_package(db, user, job.project_id, SnapshotRequest.model_validate(job.request),
                    progress=progress, job=job)
            else:
                await freeze_annotation_package(db, user, job.project_id, AnnotationSnapshotRequest.model_validate(job.request),
                    progress=progress, job=job)
        except Exception as exc:
            await db.rollback()
            job = await db.get(ExportSnapshotJob, job_id)
            if job is not None and job.status != 'completed':
                job.status = 'failed'
                job.error = str(exc.detail)[:1000] if isinstance(exc, HTTPException) else '冻结失败，请重新提交。'
                await db.commit()
            if not isinstance(exc, HTTPException):
                logger.exception('Snapshot job %s failed', job_id)
        return True


async def run_worker(sessions):
    while True:
        try:
            if await process_one(sessions):
                continue
        except Exception:
            logger.exception('Snapshot worker failed')
        await asyncio.sleep(2)


async def start_worker():
    global _worker
    from database import AsyncSessionLocal
    # shortcut: the deployment runs one backend process; use leases before running multiple instances.
    async with AsyncSessionLocal() as db:
        await db.execute(update(ExportSnapshotJob).where(ExportSnapshotJob.status.in_(ACTIVE[1:]))
            .values(status='queued', packed=0, total=0))
        await db.commit()
    _worker = asyncio.create_task(run_worker(AsyncSessionLocal))


async def stop_worker():
    if _worker:
        _worker.cancel()
        try:
            await _worker
        except asyncio.CancelledError:
            pass
