"""Milvus REST v2; only platform-owned collections may be written."""
import hashlib
import json
import httpx
from fastapi import HTTPException
from config.settings import settings
from services.image_semantics import full_vector, PREPROCESSING


def space_key(snapshot):
    return hashlib.sha256(json.dumps([snapshot['id'],snapshot['revision'],2048,PREPROCESSING]).encode()).hexdigest()


class Milvus:
    def __init__(self,client,collection=None):
        self.client=client
        self.collection=collection or settings.MILVUS_COLLECTION
        if not self.collection.startswith('image_platform_') or not self.collection.replace('_','').isalnum():
            raise HTTPException(500,'Milvus集合必须使用image_platform_前缀')

    async def call(self,operation,**body):
        try:
            response=await self.client.post(settings.MILVUS_URL.rstrip('/')+'/v2/vectordb/'+operation,
                json={'dbName':'default','collectionName':self.collection,**body},timeout=30)
            response.raise_for_status();data=response.json()
            if data.get('code')!=0:
                raise HTTPException(502,'Milvus操作失败，请检查集合状态后重试')
            return data.get('data')
        except (httpx.HTTPError,ValueError,TypeError):
            raise HTTPException(502,'Milvus不可用，请检查连接；原图和标注已保留')

    async def ensure(self):
        if not (await self.call('collections/has')).get('has'):
            await self.call('collections/create',dimension=2048,metricType='COSINE',idType='VarChar',autoID=False,
                primaryFieldName='id',vectorFieldName='vector',params={'max_length':'96','enableDynamicField':True})
        description=await self.call('collections/describe')
        field=next((f for f in description.get('fields',[]) if f.get('name')=='vector'),{})
        params=field.get('params',field.get('elementTypeParams',{}))
        if isinstance(params,list):params={p['key']:p['value'] for p in params}
        if str(params.get('dim'))!='2048' or not any(i.get('metricType')=='COSINE' for i in description.get('indexes',[])):
            raise HTTPException(409,'平台Milvus集合维度或距离配置不匹配，未改动现有集合')

    async def upsert(self,key,vector,tenant_id,project_id,asset_id,snapshot,content_hash):
        await self.ensure()
        await self.call('entities/upsert',data=[dict(id=key,vector=full_vector(vector),tenant_id=tenant_id,
            project_id=str(project_id),asset_id=str(asset_id),space=space_key(snapshot),content_hash=content_hash)])

    async def search(self,vector,tenant_id,project_id,space,keys,limit):
        if not keys:return []
        # IDs come from committed successful items, so cancelled/late vector writes stay invisible.
        common=f'tenant_id == {int(tenant_id)} and project_id == {json.dumps(str(project_id))} and space == {json.dumps(space)}'
        hits=[]
        for offset in range(0,len(keys),1000):
            result=await self.call('entities/search',data=[full_vector(vector)],annsField='vector',limit=limit,
                filter=common+' and id in '+json.dumps(keys[offset:offset+1000]),
                outputFields=['asset_id','content_hash'],searchParams={'metricType':'COSINE'},consistencyLevel='Strong')
            hits.extend(result)
        hits.sort(key=lambda hit:(-hit['distance'],hit['id']))
        return hits[:limit]
