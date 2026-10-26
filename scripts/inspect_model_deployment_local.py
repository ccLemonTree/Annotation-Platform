"""Read current schema and active tasks before any local backend restart."""
import asyncio
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend/backend'))
from sqlalchemy import text
from database import AsyncSessionLocal,engine


async def main():
    async with AsyncSessionLocal() as db:
        revision=(await db.execute(text('select version_num from alembic_version'))).scalars().all()
        training=(await db.execute(text("select id,kind,status from training_runs where status in ('starting','running','stopping','unknown')"))).mappings().all()
        validation=(await db.execute(text("select id from validation_runs where model_snapshot->'execution'->>'active' = 'true'"))).scalars().all()
        counts={}
        for table,condition in [('automation_jobs',"status in ('pending','running')"),('image_semantic_items',"status in ('pending','running')"),
            ('project_work_items',"status in ('pending','running')"),('image_uploads',"status in ('pending','processing')"),('images',"scene_status in ('pending','processing')")]:
            counts[table]=(await db.execute(text('select count(*) from '+table+' where '+condition))).scalar_one()
        if revision==['024']:
            counts['model_deployments']=(await db.execute(text("select count(*) from model_deployments where status in ('transferring','activating','rolling_back','unknown')"))).scalar_one()
        print(json.dumps(dict(revision=revision,active_training=[dict(r) for r in training],active_validation=validation,other_active_counts=counts),default=str))
    await engine.dispose()


if __name__=='__main__':asyncio.run(main())
