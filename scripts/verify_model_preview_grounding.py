"""Live upload previews; delete only this script's previews and check business data stays intact."""
import asyncio
import json
import os
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
os.chdir(ROOT/'backend/backend');sys.path.insert(0,str(Path.cwd()))
import httpx
from sqlalchemy import select,func
from database import AsyncSessionLocal
from models import User,Asset,Annotation,LibraryImage,ModelService
from services.auth import create_access_token


async def snapshot():
    async with AsyncSessionLocal() as db:
        counts={model.__name__:await db.scalar(select(func.count()).select_from(model)) for model in (Asset,Annotation,LibraryImage)}
        models=(await db.scalars(select(ModelService).order_by(ModelService.id))).all()
        from schemas.model_service import ModelServiceConfig
        return dict(counts=counts,models=[dict(id=str(row.id),revision=row.revision,config=ModelServiceConfig.model_validate(row).model_dump()) for row in models])


async def main():
    async with AsyncSessionLocal() as db:
        user=await db.scalar(select(User).where(User.role=='admin',User.is_active.is_(True)).order_by(User.id).limit(1))
        token=create_access_token(dict(user_id=user.id,tenant_id=user.tenant_id,role=user.role))
    before=await snapshot();report={'models':[]};created=[]
    image=(ROOT/'output/grounding-preview-demo.jpg').read_bytes()
    async with httpx.AsyncClient(base_url='http://127.0.0.1:8000',headers={'Authorization':'Bearer '+token},timeout=185,trust_env=False) as client:
        try:
            for row in before['models']:
                config=row['config']
                if config['key'] not in ('vlx_seek','groundingpi'):continue
                async with httpx.AsyncClient(timeout=185,trust_env=False) as remote:
                    response=await remote.post(config['base_url']+config['endpoint'],files={'image':('demo.jpg',image,'image/jpeg')},data={'labels':'["apple"]','conf':'.99','nms':'.01'})
                    response.raise_for_status();raw=response.json()
                    assert raw['score_type']=='uncalibrated' and raw['detections'],raw
                request=dict(model_id=row['id'],labels=['apple'],confidence=.99,iou=.01)
                response=await client.post('/api/model-previews',data={'request':json.dumps(request)},files={'files':('grounding-preview-demo.jpg',image,'image/jpeg')})
                response.raise_for_status();run=response.json();url='/api/model-previews/'+run['id'];created.append(url)
                assert run['config']['labels']==['apple'] and run['model']['score_type']=='uncalibrated'
                deadline=time.monotonic()+240
                while time.monotonic()<deadline:
                    response=await client.get(url);response.raise_for_status();detail=response.json()
                    if detail['status'] not in ('pending','running'):break
                    await asyncio.sleep(2)
                assert detail['status']=='completed',detail
                boxes=detail['items'][0]['predictions']
                assert boxes and all('score' not in box and box['label']=='apple' for box in boxes),boxes
                response=await client.get(url+'/items/'+detail['items'][0]['id']+'/image');response.raise_for_status()
                assert response.content==image
                report['models'].append(dict(key=config['key'],raw_boxes=len(raw['detections']),preview_boxes=len(boxes),request=request,result=detail))
                print(json.dumps(dict(key=config['key'],raw_boxes=len(raw['detections']),preview_boxes=len(boxes),unavailable_confidence=True)),flush=True)
        finally:
            for url in created:
                response=await client.delete(url);response.raise_for_status()
                assert (await client.get(url)).status_code==404
    after=await snapshot();assert before==after,'Business data or model configurations changed'
    assert len(report['models'])==2
    report.update(business_counts=after['counts'],model_configs_unchanged=True,temporary_previews_deleted=len(created))
    (ROOT/'output/model-preview-grounding-live.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':asyncio.run(main())
