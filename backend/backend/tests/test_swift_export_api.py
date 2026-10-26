import hashlib,json,zipfile
from io import BytesIO
from uuid import uuid4
import pytest
import pytest_asyncio
from PIL import Image
from models import Asset,Annotation
from models.project_work import MatchQuery


@pytest_asyncio.fixture
async def swift_project(client,test_db,test_engine,test_project,test_asset):
    from services.storage import get_storage
    async with test_engine.begin() as conn:await conn.run_sync(lambda sync:MatchQuery.__table__.create(sync,checkfirst=True))
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
