"""Read-only authenticated checks; the short-lived token stays in process memory."""
import asyncio
from datetime import timedelta
import json
from pathlib import Path
import sys
import urllib.request
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend/backend'))
from sqlalchemy import select
from database import AsyncSessionLocal,engine
from models import User
from services.auth import create_access_token


async def main():
    async with AsyncSessionLocal() as db:
        actor=await db.scalar(select(User).where(User.role=='admin',User.is_active.is_(True)).order_by(User.id).limit(1))
        assert actor is not None,'No active local administrator for read-only API verification'
        token=create_access_token(dict(user_id=actor.id,tenant_id=actor.tenant_id,username=actor.username),expires_delta=timedelta(minutes=1))
    await engine.dispose()
    def get(path):
        with urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:8000'+path,headers={'Authorization':'Bearer '+token}),timeout=90) as response:
            assert response.status==200
            return json.load(response)
    records=get('/api/model-deployments');servers=get('/api/model-deployments/servers')
    releases=get('/api/model-deployments/releases');targets=get('/api/model-deployments/targets');sources=get('/api/model-deployments/sources')
    assert isinstance(records['items'],list) and isinstance(servers,list) and servers
    assert all(set(s)<= {'name','host','user','port','group','description'} for s in servers)
    assert all(isinstance(value['items'],list) for value in (releases,targets,sources))
    print(json.dumps(dict(authenticated_records=True,authenticated_servers=True,authenticated_center=True,server_count=len(servers),records_on_page=len(records['items']),releases_on_page=len(releases['items']),targets=len(targets['items']),sources_on_page=len(sources['items']),credentials_exposed=False,http_writes=0,remote_writes=0)))


if __name__=='__main__':asyncio.run(main())
