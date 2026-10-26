"""Register the two deployed services and exercise authenticated platform previews."""
import asyncio
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT / 'backend/backend')
sys.path.insert(0, str(Path.cwd()))

import httpx
from sqlalchemy import select
from database import AsyncSessionLocal
from models import LibraryImage, User
from services.auth import create_access_token
from services.storage import get_storage


async def main():
    async with AsyncSessionLocal() as db:
        user = await db.scalar(select(User).where(User.role=='admin', User.is_active.is_(True)).order_by(User.id).limit(1))
        assert user, 'An active administrator is required'
        token = create_access_token({'user_id':user.id, 'tenant_id':user.tenant_id, 'role':user.role})
        samples = (await db.scalars(select(LibraryImage).where(LibraryImage.tenant_id==user.tenant_id,
            LibraryImage.width>=300, LibraryImage.height>=200).order_by(LibraryImage.id).limit(30))).all()
        selected = None
        for sample in samples:
            try:
                with await get_storage().get_file(sample.blob_path) as source:
                    if source.read(1):
                        selected = str(sample.id)
                        break
            except (FileNotFoundError, ValueError):
                continue
        assert selected, 'No readable existing library image for real inference'
    report = {'image_id':selected, 'models':[]}
    with httpx.Client(base_url='http://127.0.0.1:8000', headers={'Authorization':'Bearer '+token},
                      timeout=180, trust_env=False) as client:
        response = client.get('/api/model-services'); response.raise_for_status()
        known = {row['key']:row for row in response.json()}
        for key, name, port in [('vlx_seek','VLX-Seek 1.5-10B',10031), ('groundingpi','GroundingPI',10032)]:
            payload = dict(key=key, name=name, kind='detection', protocol='model_service',
                base_url=f'http://36.138.253.82:{port}', endpoint='/detect/grounding',
                timeout_seconds=180, confidence=.25, iou=.45, enabled=True,
                labels=['electric bicycle'], label_mapping={'electric bicycle':'电瓶车'})
            if key in known:
                record = known[key]
                assert all(record.get(k)==v for k,v in payload.items()), f'Existing {key} configuration differs; refusing overwrite'
            else:
                created = client.post('/api/model-services', json=payload); created.raise_for_status()
                record = created.json()
            test = client.post('/api/model-services/'+record['id']+'/test'); test.raise_for_status()
            result = client.post('/api/model-services/'+record['id']+'/predict',
                json={'image_id':selected, 'prompt':'electric bicycle; person; car'})
            result.raise_for_status()
            data = result.json()
            assert data['score_type']=='uncalibrated' and all('score' not in box for box in data['boxes'])
            assert any('未提供' in warning for warning in data['warnings'])
            report['models'].append({'key':key,'id':record['id'],'base_url':payload['base_url'],
                'test':test.json(),'prediction':data})
            print(json.dumps({'key':key,'test':test.status_code,'predict':result.status_code,
                              'box_count':len(data['boxes'])}, ensure_ascii=False), flush=True)
    destination = ROOT / 'output/grounding-deployment-20261020.json'
    destination.write_text(json.dumps(report,ensure_ascii=False,indent=2), encoding='utf-8')


if __name__ == '__main__':
    asyncio.run(main())
