"""Read-only local activity and business-row fingerprints around the migration."""
import argparse
import asyncio
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend/backend'))
from sqlalchemy import text
from database import AsyncSessionLocal,engine


async def main():
    parser=argparse.ArgumentParser();parser.add_argument('--save');parser.add_argument('--compare');args=parser.parse_args()
    async with AsyncSessionLocal() as db:
        revision=(await db.execute(text('select version_num from alembic_version'))).scalar_one()
        hashes={}
        for table in ('images','assets','annotations','projects','image_collections','export_snapshots','training_runs'):
            row=(await db.execute(text("select count(*) as count,md5(coalesce(string_agg(md5(row_to_json(t)::text),'' order by id),'')) as hash from "+table+' t'))).mappings().one()
            hashes[table]=dict(row)
        active={}
        for table,condition in [('training_runs',"status in ('starting','running','stopping','unknown')"),('validation_runs',"model_snapshot->'execution'->>'active'='true'"),
            ('automation_jobs',"status in ('pending','running')"),('image_semantic_items',"status in ('pending','running')"),('project_work_items',"status in ('pending','running')"),
            ('image_uploads',"status in ('pending','processing')"),('images',"scene_status in ('pending','processing')"),('model_deployments',"status in ('transferring','activating','rolling_back','unknown')")]:
            active[table]=(await db.execute(text('select count(*) from '+table+' where '+condition))).scalar_one()
        if revision=='025':active['model_releases']=(await db.execute(text("select count(*) from model_releases where status='archiving'"))).scalar_one()
        result=dict(revision=revision,business=hashes,active=active)
        if args.save:
            path=Path(args.save);path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(result,indent=2),encoding='utf-8')
        if args.compare:assert hashes==json.loads(Path(args.compare).read_text(encoding='utf-8'))['business'],'Business data changed'
        print(json.dumps(dict(revision=revision,active=active,business_unchanged=True if args.compare else None)))
        assert not any(active.values()),'Active work: do not restart the backend'
    await engine.dispose()


if __name__=='__main__':asyncio.run(main())
