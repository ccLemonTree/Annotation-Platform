"""Only creates/drops this invocation's explicitly owned smoke collection."""
import asyncio
import json
import sys
from pathlib import Path
from uuid import uuid4
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'/'backend'))
import httpx
from services.milvus import Milvus,space_key


async def main():
    name='image_platform_smoke_'+uuid4().hex
    async with httpx.AsyncClient(follow_redirects=False) as client:
        m=Milvus(client,collection=name)
        assert not (await m.call('collections/has'))['has']
        owned=False
        try:
            await m.ensure();owned=True
            snapshot={'id':str(uuid4()),'revision':1};project=uuid4();asset=uuid4()
            await m.upsert('owned_one',[1.]+[0.]*2047,1,project,asset,snapshot,'one')
            await m.upsert('owned_two',[0.,1.]+[0.]*2046,1,project,uuid4(),snapshot,'two')
            hits=await m.search([1.]+[0.]*2047,1,project,space_key(snapshot),['owned_one','owned_two'],2)
            assert [h['id'] for h in hits]==['owned_one','owned_two'],hits
            assert await m.search([1.]+[0.]*2047,2,project,space_key(snapshot),['owned_one'],2)==[]
            print(json.dumps({'collection':name,'inserted':2,'retrieved':len(hits),'tenant_isolation':True}))
        finally:
            if owned:
                await m.call('collections/drop')
                assert not (await m.call('collections/has'))['has']
                print('Owned smoke collection removed')

asyncio.run(main())
