"""Read-only deployment checks; never stores authentication or modifies platform data."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
os.chdir(ROOT/'backend/backend')
sys.path.insert(0,str(Path.cwd()))
import httpx
from sqlalchemy import cast,func,inspect,select,String
from database import AsyncSessionLocal
from models import Base,User
from services.auth import create_access_token


async def main(phase):
    report={'phase':phase,'active':{},'records':{}}
    async with AsyncSessionLocal() as db:
        conn=await db.connection()
        existing=await conn.run_sync(lambda sync:set(inspect(sync).get_table_names()))
        for name,table in Base.metadata.tables.items():
            if name not in existing:continue
            if name in ('images','image_collections','assets','annotations','export_snapshots','training_runs','training_gpu_holds'):
                rows=(await db.execute(select(*table.primary_key.columns))).all()
                report['records'][name]={'count':len(rows),'ids_sha256':hashlib.sha256(json.dumps(sorted(str(tuple(row)) for row in rows)).encode()).hexdigest()}
            if 'status' in table.c:
                status=cast(table.c.status,String)
                rows=(await db.execute(select(status,func.count()).where(status.in_(
                    ['pending','queued','running','starting','stopping','unknown'])).group_by(status))).all()
                if rows:report['active'][name]=dict(rows)
        user=await db.scalar(select(User).where(User.role=='admin',User.is_active.is_(True)).order_by(User.id).limit(1))
        assert user is not None,'No active admin for read-only catalog check'
        token=create_access_token({'user_id':user.id,'tenant_id':user.tenant_id,'role':user.role})
    with httpx.Client(base_url='http://127.0.0.1:8000',trust_env=False,timeout=20) as client:
        health=client.get('/health');health.raise_for_status();report['health']=health.status_code
        schema=client.get('/openapi.json');schema.raise_for_status()
        report['training_kinds']=schema.json()['components']['schemas']['TrainingCreate']['properties']['kind']['enum']
        catalog=client.get('/api/training/catalog',headers={'Authorization':'Bearer '+token});catalog.raise_for_status()
        report['catalog_kinds']=list(catalog.json())
        if phase=='after':
            assert catalog.json()['dinov3_vits16_ltdetr']['image']=='lightly/train:latest'
            assert 'dinov3_vits16_ltdetr' in report['training_kinds']
            before=json.loads((ROOT/'output/dinov3-runtime-before.json').read_text(encoding='utf-8'))
            assert report['records']==before['records'],'Existing data or training IDs changed'
    destination=ROOT/f'output/dinov3-runtime-{phase}.json'
    destination.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':asyncio.run(main(sys.argv[1]))
