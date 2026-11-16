"""Move legacy freeze records to storage. Run from the backend with python -m."""
import asyncio

from sqlalchemy import select

from database import AsyncSessionLocal
from models import AnnotationEvent, ExportSnapshot
from services.snapshot_manifests import manifest_summary, read_manifest, write_manifest


async def publish(manifest, path):
    await write_manifest(manifest, path)
    if await read_manifest(path) != manifest:
        raise RuntimeError(f'Frozen manifest verification failed: {path}')


async def migrate(db):
    events = select(AnnotationEvent).where(
        AnnotationEvent.action == 'freeze_export',
        AnnotationEvent.detail['manifest_path'].as_string().is_(None))
    snapshot_ids = (await db.scalars(select(ExportSnapshot.id).where(
        ExportSnapshot.manifest_path.is_(None)))).all()
    snapshots = audit_events = 0
    for snapshot_id in snapshot_ids:
        snapshot = await db.get(ExportSnapshot, snapshot_id, with_for_update=True)
        if snapshot is None or snapshot.manifest_path:
            continue
        manifest = await read_manifest(None, snapshot.manifest)
        path = f'exports/manifests/{snapshot.id}.json'
        await publish(manifest, path)
        snapshot.manifest_path = path
        snapshot.list_metadata = manifest_summary(manifest)
        snapshot.manifest = None
        await db.commit()
        snapshots += 1

    event_ids = (await db.scalars(events.with_only_columns(AnnotationEvent.id))).all()
    for event_id in event_ids:
        event = await db.get(AnnotationEvent, event_id, with_for_update=True)
        if event is None or event.detail.get('manifest_path'):
            continue
        manifest = dict(event.detail)
        snapshot_id = manifest.pop('snapshot_id')
        path = await db.scalar(select(ExportSnapshot.manifest_path).where(
            ExportSnapshot.id == snapshot_id, ExportSnapshot.project_id == event.project_id))
        if not path or await read_manifest(path) != manifest:
            path = f'exports/manifests/history/{event.id}.json'
            await publish(manifest, path)
        event.detail = dict(manifest_summary(manifest), snapshot_id=snapshot_id, manifest_path=path,
                            format=manifest.get('format'))
        await db.commit()
        audit_events += 1
    print(f'Migrated snapshots: {snapshots}, audit events: {audit_events}')


async def main():
    async with AsyncSessionLocal() as db:
        await migrate(db)


if __name__ == '__main__':
    asyncio.run(main())
