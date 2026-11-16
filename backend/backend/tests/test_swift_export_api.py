import hashlib,json,zipfile
from io import BytesIO
from uuid import uuid4
import pytest
import pytest_asyncio
from PIL import Image
from models import Asset,Annotation,ProjectWorkJob,ProjectWorkItem
from models.project_work import MatchQuery


@pytest_asyncio.fixture
async def swift_project(client,test_db,test_engine,test_project,test_asset):
    from services.storage import get_storage
    async with test_engine.begin() as conn:
        for model in (MatchQuery,ProjectWorkJob,ProjectWorkItem):
            await conn.run_sync(lambda sync:model.__table__.create(sync,checkfirst=True))
    test_project.task_type='vector';test_project.label_schema={'labels':['黑衣'],'annotation_scope':'crops'}
    async def picture(asset,color):
        raw=BytesIO();Image.new('RGB',(64,48),color).save(raw,'PNG');content=raw.getvalue()
        asset.content_hash=hashlib.sha256(content).hexdigest();asset.file_size=len(content);asset.format='PNG'
        await get_storage().save_file(BytesIO(content),asset.blob_path)
    await picture(test_asset,'purple')
    crops=[]
    for color in ['red','blue','white','yellow']:
        asset=Asset(project_id=test_project.id,original_name=color+'.png',blob_path='owned/'+color+'.png',content_hash=color,
            source_type='model_crop',width=64,height=48,file_size=1,annotation_status='pending')
        await picture(asset,color);test_db.add(asset);crops.append(asset)
    await test_db.flush()
    test_db.add_all([Annotation(asset_id=asset.id,version=1,is_latest=True,review_status='approved',reviewer_id=1,
        annotation_data={'description':'穿黑衣服的人','labels':['黑衣']}) for asset in [test_asset,crops[0]]])
    def hit(asset,judgment):return {'asset_id':str(asset.id),'content_hash':asset.content_hash,'score':.98,'judgment':judgment,'review_status':'approved'}
    test_db.add_all([MatchQuery(project_id=test_project.id,query={'text':'黑衣服的人'},space={'revision':1},
        hits=[hit(crops[0],'correct'),hit(crops[1],'correct'),hit(crops[2],'incorrect'),hit(crops[3],'unknown'),hit(test_asset,'correct')]),
        MatchQuery(project_id=test_project.id,query={'image_id':str(uuid4()),'blob_path':test_asset.blob_path,'content_hash':test_asset.content_hash},
            space={'revision':1},hits=[hit(crops[0],'correct')])])
    await test_db.commit()
    return test_project,crops


@pytest.mark.asyncio
@pytest.mark.parametrize('format,source,count',[('swift_sft','descriptions',1),('infonce','descriptions',1),('infonce','labels',1),
    ('infonce','matches',3),('contrastive','matches',4),('online_contrastive','matches',4),('cosine_similarity','matches',4)])
async def test_api_downloads_aligned_swift_rows_and_readable_images(client,auth_headers,swift_project,format,source,count):
    project,crops=swift_project
    response=client.get(f'/api/export/projects/{project.id}/export',headers=auth_headers,
        params={'format':'annotation','dataset_format':format,'sample_source':source,'instruction':'描述<image>中的衣服'})
    assert response.status_code==200,response.text
    with zipfile.ZipFile(BytesIO(response.content)) as archive:
        rows=[json.loads(line) for line in archive.read('train.jsonl').decode().splitlines()]
        assert len(rows)==count
        manifest=json.loads(archive.read('manifest.json'))
        assert manifest['training']['samples']==count
        assert 'README.txt' in archive.namelist()
        assert all(entry['asset_id'] in {str(crop.id) for crop in crops} for entry in manifest['assets'])
        for row in rows:
            for prefix in ['','positive_','negative_']:
                messages=row.get(prefix+'messages',[]);images=row.get(prefix+'images',[])
                sequences=[messages] if not prefix else messages
                paths=[images] if not prefix else images
                assert len(sequences)==len(paths)
                for sequence,files in zip(sequences,paths):
                    assert sum(message['content'].count('<image>') for message in sequence)==len(files)
                    for path in files:
                        with Image.open(BytesIO(archive.read(path))) as image:image.convert('RGB').load()
            if format=='swift_sft':assert row['messages'][-1]=={'role':'assistant','content':'穿黑衣服的人'}
            else:
                assert len(row['positive_messages'])==len(row['positive_images'])==1
                assert 'label' not in row if format=='infonce' else row['label'] in (0,1)


def test_export_parameter_validation(client,auth_headers,test_project):
    path=f'/api/export/projects/{test_project.id}/export'
    assert client.get(path,headers=auth_headers,params={'format':'annotation','dataset_format':'typo'}).status_code==422
    assert client.get(path,headers=auth_headers,params={'format':'annotation','sample_source':'typo'}).status_code==422


@pytest.mark.asyncio
async def test_fixed_person_captions_and_structured_attributes_share_single_attribute_export(client,auth_headers,swift_project,test_db):
    project,crops=swift_project
    project.task_type='caption'
    from sqlalchemy import select
    current=await test_db.scalar(select(Annotation).where(Annotation.asset_id==crops[0].id))
    current.annotation_data={'description':json.dumps({'体型':['标准'],'穿着':'黑色外套','配饰':'无'},ensure_ascii=False)}
    current.review_status='pending'
    test_db.add_all([
        Annotation(asset_id=crops[1].id,version=1,is_latest=True,review_status='approved',annotation_data={
            'attribute_schema':{'target_label':'person','fields':[{'key':'body_type','name':'体型','options':['标准','偏瘦']}]},
            'attributes':{'body_type':{'value':'偏瘦','reason':''}}}),
        Annotation(asset_id=crops[2].id,version=1,is_latest=True,review_status='pending',annotation_data={
            'description':json.dumps({'体型':['标准','偏胖'],'穿着':'无上衣，蓝色牛仔裤'},ensure_ascii=False)}),
        Annotation(asset_id=crops[3].id,version=1,is_latest=True,review_status='rejected',annotation_data={
            'description':json.dumps({'体型':['偏胖']},ensure_ascii=False)}),
    ])
    await test_db.commit()
    path=f'/api/export/projects/{project.id}'
    query=dict(dataset_format='infonce',sample_source='single_attribute',approved_only=False,
        single_attribute={'semantics':'both','negative_count':1})
    preview=client.post(path+'/annotation-snapshots/preview',headers=auth_headers,json=query).json()
    assert preview['asset_count']==2 and preview['sample_count']==4
    response=client.post(path+'/annotation-snapshots',headers=auth_headers,json=query|{'preview_token':preview['preview_token']})
    assert response.status_code==201,response.text
    snapshot=client.get(f"/api/export/snapshots/{response.json()['id']}/download",headers=auth_headers)
    with zipfile.ZipFile(BytesIO(snapshot.content)) as archive:
        lines=[json.loads(line) for line in archive.read('train.jsonl').splitlines()]
    reverse=next(row for row in lines if '不是标准' in row['messages'][0]['content'])
    assert str(crops[1].id) in reverse['positive_images'][0][0]
    assert str(crops[0].id) in reverse['negative_images'][0][0]
    positive_query=query|{'single_attribute':{'semantics':'forward','negative_count':0}}
    result=client.get(path+'/export',headers=auth_headers,params=positive_query|{'format':'annotation',
        'approved_only':'false','single_attribute':json.dumps(positive_query['single_attribute'])})
    assert result.status_code==200,result.text
    with zipfile.ZipFile(BytesIO(result.content)) as archive:
        lines=[json.loads(line) for line in archive.read('train.jsonl').splitlines()]
    assert any('穿着为黑色外套' in row['messages'][0]['content'] for row in lines)
    assert all('negative_images' not in row for row in lines)
    reverse_only=client.get(path+'/export',headers=auth_headers,params=positive_query|{'format':'annotation',
        'approved_only':'false','single_attribute':json.dumps({'semantics':'reverse','negative_count':0})})
    assert reverse_only.status_code==200,reverse_only.text
    with zipfile.ZipFile(BytesIO(reverse_only.content)) as archive:
        lines=[json.loads(line) for line in archive.read('train.jsonl').splitlines()]
    assert len(lines)==4
    assert all('negative_images' not in row for row in lines)
    assert any(row['messages'][0]['content']=='图中这名人员没有配饰。' for row in lines)
    assert any(row['messages'][0]['content']=='图中这名人员没有穿上衣。' for row in lines)


@pytest.mark.asyncio
@pytest.mark.parametrize('format',['infonce','contrastive','online_contrastive','cosine_similarity'])
@pytest.mark.parametrize('reverse',[False,True])
async def test_single_attribute_export(client,auth_headers,swift_project,test_db,format,reverse):
    from sqlalchemy import select
    project,crops=swift_project
    for index,(asset,value) in enumerate(zip(crops,['白','白色','黑色','蓝色'])):
        data=dict(attributes={'body_color':dict(value=value,reason='')},
            attribute_schema=dict(target_label='car',fields=[dict(key='body_color',name='车身颜色',options=[])]))
        if index==0:
            row=await test_db.scalar(select(Annotation).where(Annotation.asset_id==asset.id))
            row.annotation_data=data
        else:
            test_db.add(Annotation(asset_id=asset.id,version=1,is_latest=True,review_status='approved',annotation_data=data))
    await test_db.commit()
    query=dict(key='body_color',query_value='白色',positive_count=2,negative_count=2,reverse=reverse)
    path=f'/api/export/projects/{project.id}'
    params=dict(dataset_format=format,sample_source='single_attribute')
    preview=client.post(path+'/annotation-snapshots/preview',headers=auth_headers,json=params|{'single_attribute':query})
    assert preview.status_code==200,preview.text
    assert preview.json()['sample_count']==(2 if format=='infonce' else 4)
    response=client.get(path+'/export',headers=auth_headers,params=params|{'format':'annotation','single_attribute':json.dumps(query)})
    assert response.status_code==200,response.text
    positives={str(asset.id) for asset in (crops[2:] if reverse else crops[:2])}
    negatives={str(asset.id) for asset in (crops[:2] if reverse else crops[2:])}
    with zipfile.ZipFile(BytesIO(response.content)) as archive:
        rows=[json.loads(line) for line in archive.read('train.jsonl').splitlines()]
        for row in rows:
            assert ('不是' in row['messages'][0]['content'])==reverse
            assert len(row['positive_images'])==1
            assert row['positive_images'][0][0].split('/')[-1].split('.')[0] in (negatives if row.get('label')==0 else positives)
            if format=='infonce':
                assert {path[0].split('/')[-1].split('.')[0] for path in row['negative_images']}==negatives
        assert len(rows)==preview.json()['sample_count']
    frozen=client.post(path+'/annotation-snapshots',headers=auth_headers,
        json=params|dict(single_attribute=query,preview_token=preview.json()['preview_token'],version_label='车辆属性测试'))
    assert frozen.status_code==201,frozen.text
    assert client.get(path+'/snapshots',headers=auth_headers,params={'training_only':True}).json()[0]['version_label']=='车辆属性测试'
    summary=client.get(path+'/snapshots',headers=auth_headers,params={'summary_only':True}).json()[0]
    assert summary['version_label']=='车辆属性测试'
    assert summary['sample_count']==preview.json()['sample_count'] and summary['asset_count']==4
    assert 'manifest' not in summary


@pytest.mark.asyncio
@pytest.mark.parametrize('format',['infonce','contrastive','online_contrastive','cosine_similarity'])
async def test_single_attribute_exports_only_positives_with_no_other_values(client,auth_headers,swift_project,test_db,format):
    from sqlalchemy import select
    from models import ExportSnapshot
    from services.storage import get_storage
    from uuid import UUID
    project,crops=swift_project
    ann=await test_db.scalar(select(Annotation).where(Annotation.asset_id==crops[0].id))
    ann.annotation_data=dict(attributes={'body_color':dict(value='白色',reason='')},
        attribute_schema=dict(target_label='car',fields=[dict(key='body_color',name='车身颜色')]))
    await test_db.commit()
    path=f'/api/export/projects/{project.id}'
    selection=dict(semantics='forward',sample_limit=1,negative_count=0)
    params=dict(dataset_format=format,sample_source='single_attribute',single_attribute=selection)
    preview=client.post(path+'/annotation-snapshots/preview',headers=auth_headers,json=params)
    assert preview.status_code==200,preview.text
    assert preview.json()['sample_count']==1 and preview.json()['negative_image_count']==0
    response=client.get(path+'/export',headers=auth_headers,
        params=params|{'format':'annotation','single_attribute':json.dumps(selection)})
    assert response.status_code==200,response.text
    frozen=client.post(path+'/annotation-snapshots',headers=auth_headers,
        json=params|{'preview_token':preview.json()['preview_token']})
    assert frozen.status_code==201,frozen.text
    assert 'negative_strategy' not in frozen.json()['manifest']['training']
    snapshot=await test_db.get(ExportSnapshot,UUID(frozen.json()['id']))
    for stream in (BytesIO(response.content),await get_storage().get_file(snapshot.blob_path)):
        with stream,zipfile.ZipFile(stream) as archive:
            row=json.loads(archive.read('train.jsonl'))
            assert '白色' in row['messages'][0]['content']
            assert not {'negative_messages','negative_images'} & row.keys()
            assert row.get('label')==(None if format=='infonce' else 1)


@pytest.mark.asyncio
async def test_auto_single_attributes_include_all_fields_and_limit_reverse_only(client,auth_headers,swift_project,test_db):
    from sqlalchemy import select
    project,crops=swift_project
    for asset,color,brand in zip(crops,['白色','白色','黑色','黑色'],['奔驰','奥迪','奔驰','奥迪']):
        ann=await test_db.scalar(select(Annotation).where(Annotation.asset_id==asset.id))
        if not ann:
            ann=Annotation(asset_id=asset.id,version=1,is_latest=True,review_status='approved')
            test_db.add(ann)
        ann.annotation_data=dict(attributes={'body_color':dict(value=color,reason=''),'brand':dict(value=brand,reason='')},
            attribute_schema=dict(target_label='car',fields=[dict(key='body_color',name='车身颜色'),dict(key='brand',name='车辆品牌')]))
    await test_db.commit()
    path=f'/api/export/projects/{project.id}'
    for semantics,limit,count,negative_count in [('both',None,16,1),('reverse',3,3,1),('reverse',3,3,0)]:
        selection=dict(semantics=semantics,sample_limit=limit,negative_count=negative_count)
        params=dict(dataset_format='infonce',sample_source='single_attribute')
        preview=client.post(path+'/annotation-snapshots/preview',headers=auth_headers,json=params|{'single_attribute':selection})
        assert preview.status_code==200,preview.text
        assert preview.json()['sample_count']==count
        response=client.get(path+'/export',headers=auth_headers,
            params=params|{'format':'annotation','single_attribute':json.dumps(selection)})
        assert response.status_code==200,response.text
        with zipfile.ZipFile(BytesIO(response.content)) as archive:
            rows=[json.loads(line) for line in archive.read('train.jsonl').splitlines()]
            texts=[row['messages'][0]['content'] for row in rows]
            assert len(rows)==count
            assert all(len(row['positive_images'])==1 and len(row.get('negative_images',[]))==negative_count for row in rows)
            assert all('不是' in text for text in texts) if semantics=='reverse' else (
                any('不是' in text for text in texts) and any('为' in text for text in texts) and
                any('车身颜色' in text for text in texts) and any('车辆品牌' in text for text in texts))


@pytest.mark.asyncio
async def test_reverse_plate_types_do_not_compare_aliases_or_ambiguous_values(client,auth_headers,swift_project,test_db):
    from sqlalchemy import select
    project,crops=swift_project
    for asset,value in zip(crops,['小型新能源汽车','新能源小型汽车号牌','普通小型汽车号牌','新能源车牌']):
        ann=await test_db.scalar(select(Annotation).where(Annotation.asset_id==asset.id))
        if not ann:
            ann=Annotation(asset_id=asset.id,version=1,is_latest=True,review_status='approved')
            test_db.add(ann)
        ann.annotation_data=dict(attributes={'plate_type':dict(value=value,reason='')},
            attribute_schema=dict(target_label='car',fields=[dict(key='plate_type',name='车牌类型')]))
    await test_db.commit()
    path=f'/api/export/projects/{project.id}/export'
    response=client.get(path,headers=auth_headers,params=dict(format='annotation',dataset_format='infonce',
        sample_source='single_attribute',single_attribute=json.dumps(dict(semantics='reverse',negative_count=0))))
    assert response.status_code==200,response.text
    with zipfile.ZipFile(BytesIO(response.content)) as archive:
        rows=[json.loads(line) for line in archive.read('train.jsonl').splitlines()]
    assert len(rows)==2
    for row in rows:
        actual=row['positive_images'][0][0].split('/')[-1].split('.')[0]
        sentence=row['messages'][0]['content']
        assert actual!=str(crops[3].id)
        assert ('新能源' in sentence)==(actual==str(crops[2].id))
