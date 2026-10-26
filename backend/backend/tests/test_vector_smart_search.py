import importlib.util
import json
import subprocess
import sys
import zipfile
from io import BytesIO
from uuid import UUID,uuid4

import httpx
import pytest
from PIL import Image
from tests.test_project_work import work_env
from tests.test_validation import validation_tables


def picture():
    image=Image.new('RGB',(100,80),'blue')
    image.paste('red',(25,20,75,60))
    output=BytesIO();image.save(output,'PNG')
    return output.getvalue()


def test_crop_helpers_import_without_loading_http_routes():
    result=subprocess.run([sys.executable,'-c','from services.vector_smart_search import crop_image'],capture_output=True,text=True)
    assert result.returncode==0,result.stderr


def record(**updates):
    return dict(imageUrl='http://search.test/picture.png',largeImageUrl='http://search.test/picture.png',
        x1=.25,y1=.25,x2=.75,y2=.75,confidence=.8,deviceName='摄像头1',timestamp='2026-10-23 12:00:00',**updates)


def test_search_keeps_records_and_uses_cut_contract(monkeypatch):
    from integrations.smarteye.api import smart_search as remote
    calls=[]
    class Response:
        def raise_for_status(self):pass
        def json(self):return {'code':0,'data':[record()]}
    def post(url,**kwargs):calls.append((url,kwargs['json']));return Response()
    monkeypatch.setattr(remote.requests,'post',post)
    platform={'base_url':'http://search.test','platform_name':'test'}
    result=remote.search_platform(platform,{},dict(text='人',limit=1,startTime='a',endTime='b'))
    assert result.get('records')==[record()]
    assert result['images']==['http://search.test/picture.png']
    body=dict(picUrl='http://search.test/query.png',x=25,y=20,width=50,height=40,startTime='a',endTime='b')
    result=remote.search_platform(platform,{},body)
    assert calls[-1]==('http://search.test/api/board/aiNvr/search/cut',body)
    assert result['records']==[record()]


def test_one_malformed_upstream_record_does_not_hide_valid_records(monkeypatch):
    from integrations.smarteye.api import smart_search as remote
    class Response:
        def raise_for_status(self):pass
        def json(self):return {'code':0,'data':[None,record()]}
    monkeypatch.setattr(remote.requests,'post',lambda *args,**kwargs:Response())
    result=remote.search_platform({'base_url':'http://search.test'},{},dict(text='人'))
    assert result['success'] is True
    assert result['records']==[None,record()]
    assert result['images']==[record()['imageUrl']]


def test_crop_is_normalized_clamped_and_rejects_invalid_boxes():
    assert importlib.util.find_spec('services.vector_smart_search'),'智搜标注 service 尚未实现'
    from services.vector_smart_search import crop_image
    raw,region=crop_image(picture(),record(),'test')
    assert region==[25,20,75,60]
    with Image.open(BytesIO(raw)) as image:
        assert image.size==(50,40)
        assert image.getpixel((0,0))==(255,0,0)
    for change in ({'x1':float('nan')},{'x2':.1},{'y2':None},{'x2':2}):
        row=record();row.update(change)
        with pytest.raises(ValueError):crop_image(picture(),row,'test')


@pytest.fixture
def upstream(monkeypatch):
    assert importlib.util.find_spec('services.vector_smart_search'),'智搜标注 service 尚未实现'
    from services import vector_smart_search as service
    from integrations.smarteye.api import smart_search as remote
    platform=dict(plat_id='p1',platform_name='杭州移动消防平台',base_url='http://search.test',state='1',password='hidden')
    monkeypatch.setattr(service,'get_smart_search_platforms',lambda **kwargs:[platform])
    monkeypatch.setattr(remote,'login_platform',lambda p:{'success':True,'token':'hidden'})
    state={'rows':[record()], 'calls':[]}
    def search(p,token,params):
        state['calls'].append(params)
        return dict(success=True,records=state['rows'],images=[r['imageUrl'] for r in state['rows']])
    monkeypatch.setattr(remote,'search_platform',search)
    download=service.download_image
    async def fetched(client,url):
        def response(request):
            if request.url.path=='/bad.png':return httpx.Response(200,content=b'not an image')
            return httpx.Response(200,content=picture())
        async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as mock:
            return await download(mock,url)
    monkeypatch.setattr(service,'download_image',fetched)
    return state


@pytest.mark.asyncio
async def test_search_crops_and_saves_history_without_index_then_judges(client,auth_headers,work_env,upstream):
    from models import Asset
    from services.storage import get_storage
    s=work_env;s.project.task_type='vector';s.project.label_schema={'annotation_scope':'crops'};await s.db.commit()
    path=f'/api/projects/{s.project.id}/matches/smart-search'
    payload=dict(platform_id='p1',text='人',limit=20,start_time='2026-10-23 00:00:00',end_time='2026-10-23 18:00:00')
    upstream['rows']=[record(),{**record(),'imageUrl':'http://search.test/bad.png'}, {**record(),'x2':.1}]
    response=client.post(path,headers=auth_headers,json=payload)
    assert response.status_code==201,response.text
    query=response.json()
    assert len(query['hits'])==1 and len(query['failures'])==2
    assert query['space']['source']=='smart_search' and 'hidden' not in response.text
    hit=query['hits'][0]
    assert hit['judgment']=='unknown' and hit['smart_search']['region']==[25,20,75,60]
    asset=await s.db.get(Asset,UUID(hit['asset_id']))
    assert asset.source_type=='model_crop' and asset.image_id is None
    with await get_storage().get_file(asset.blob_path) as file,Image.open(file) as image:assert image.size==(50,40)
    await s.db.commit()
    response=client.put(f'/api/projects/{s.project.id}/matches/{query["id"]}',headers=auth_headers,
        json=dict(asset_id=hit['asset_id'],judgment='correct',base_version=query['version']))
    assert response.status_code==200,response.text
    response=client.post(path,headers=auth_headers,json=payload)
    assert response.status_code==201,response.text
    assert response.json()['hits'][0]['asset_id']==hit['asset_id']
    assert response.json()['hits'][0]['judgment']=='unknown','每次查询单独保存判定'
    history=client.get(f'/api/projects/{s.project.id}/matches',headers=auth_headers).json()
    assert len(history)==2 and len(history[0]['hits'])==1
    assert client.post(path,json=payload).status_code==401
    assert client.post(f'/api/projects/{uuid4()}/matches/smart-search',headers=auth_headers,json=payload).status_code==404
    assert client.post(path,headers=auth_headers,json={**payload,'platform_id':'missing'}).status_code==404
    assert client.post(path,headers=auth_headers,json={**payload,'end_time':'2026-10-22 00:00:00'}).status_code==422


@pytest.mark.asyncio
async def test_image_search_uses_owned_original_pixel_region_and_exports_reference(client,auth_headers,work_env,upstream,validation_tables):
    s=work_env;s.project.task_type='vector';s.project.label_schema={'annotation_scope':'crops'};await s.db.commit()
    base=f'/api/projects/{s.project.id}/matches'
    args=dict(platform_id='p1',text='人',start_time='2026-10-23 00:00:00',end_time='2026-10-23 18:00:00')
    first=client.post(base+'/smart-search',headers=auth_headers,json=args).json();hit=first['hits'][0]
    source=client.get(base+f'/{first["id"]}/source/{hit["asset_id"]}',headers=auth_headers)
    assert source.status_code==200
    with Image.open(BytesIO(source.content)) as image:assert image.size==(100,80)
    assert client.get(base+f'/{uuid4()}/source/{hit["asset_id"]}',headers=auth_headers).status_code==404
    region=dict(x=10,y=15,width=60,height=50)
    body={k:v for k,v in args.items() if k!='text'}
    body.update(reference_query_id=first['id'],reference_asset_id=hit['asset_id'],region=region)
    response=client.post(base+'/smart-search',headers=auth_headers,json=body)
    assert response.status_code==201,response.text
    query=response.json()
    assert upstream['calls'][-1]==dict(picUrl='http://search.test/picture.png',**region,
        startTime=args['start_time'],endTime=args['end_time'])
    assert query['query']['query_asset_id'] and query['query']['content_hash']
    # Existing matching-review and export must receive the selected crop as the query image.
    from services.match_review import query_reference
    reference=await query_reference(s.db,s.project,query['query'])
    assert (reference.width,reference.height)==(60,50)
    await s.db.commit()
    h=query['hits'][0]
    updated=client.put(base+'/'+query['id'],headers=auth_headers,json=dict(asset_id=h['asset_id'],judgment='correct',base_version=query['version'])).json()
    response=client.post(base+'/'+query['id']+'/review',headers=auth_headers,json=dict(asset_id=h['asset_id'],approved=True,base_version=updated['version']))
    assert response.status_code==200,response.text
    response=client.get(f'/api/export/projects/{s.project.id}/export',headers=auth_headers,
        params=dict(format='annotation',dataset_format='infonce',sample_source='matches'))
    assert response.status_code==200,response.text
    with zipfile.ZipFile(BytesIO(response.content)) as archive:
        row=json.loads(archive.read('train.jsonl').decode().splitlines()[0])
        with Image.open(BytesIO(archive.read(row['images'][0]))) as image:assert image.size==(60,50)
    from schemas.validation import ValidationSetCreate
    from services.multimodal_validation import create_set
    dataset=await create_set(s.db,ValidationSetCreate(name='隔离验证',project_id=s.project.id,asset_ids=[UUID(h['asset_id'])]),s.user,s.project)
    image_query=next(q for q in dataset.source['queries'] if q['kind']=='image')
    sample=next(sample for sample in dataset.samples if sample['asset_id']==image_query['asset_id'])
    assert sample['candidate'] is False and (sample['width'],sample['height'])==(60,50)
    await s.db.commit()
    assert client.post(base+'/smart-search',headers=auth_headers,json={**body,'reference_query_id':str(uuid4())}).status_code==404
    assert client.post(base+'/smart-search',headers=auth_headers,json={**body,'region':dict(x=90,y=0,width=20,height=20)}).status_code==422


def test_image_download_refuses_untrusted_origin_and_empty_results(monkeypatch):
    assert importlib.util.find_spec('services.vector_smart_search')
    from services.vector_smart_search import image_url
    assert image_url('/picture.png','http://search.test')=='http://search.test/picture.png'
    for url in ('http://169.254.169.254/secret','file:///etc/passwd','http://user:pass@search.test/a'):
        with pytest.raises(ValueError):image_url(url,'http://search.test')


@pytest.mark.asyncio
async def test_empty_and_all_failed_results_are_saved_without_fake_hits(client,auth_headers,work_env,upstream):
    s=work_env;s.project.task_type='vector';await s.db.commit()
    path=f'/api/projects/{s.project.id}/matches/smart-search'
    payload=dict(platform_id='p1',text='人',start_time='2026-10-23 00:00:00',end_time='2026-10-23 18:00:00')
    platforms=client.get(path+'/platforms',headers=auth_headers)
    assert platforms.json()==[{'id':'p1','name':'杭州移动消防平台'}]
    assert client.get(path+'/platforms').status_code==401
    for rows in ([],[{**record(),'imageUrl':'http://search.test/bad.png'}]):
        upstream['rows']=rows
        response=client.post(path,headers=auth_headers,json=payload)
        assert response.status_code==201,response.text
        assert response.json()['hits']==[]
        assert len(response.json()['space']['failures'])==len(rows)
    calls=len(upstream['calls'])
    assert client.post(path,headers=auth_headers,json={**payload,'reference_query_id':str(uuid4())}).status_code==422
    assert len(upstream['calls'])==calls
