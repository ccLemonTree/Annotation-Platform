"""Isolated freeze/export contracts; no business DB, SSH or training calls."""
import hashlib,json,zipfile
from io import BytesIO

import pytest
from fastapi import HTTPException
from PIL import Image


@pytest.mark.asyncio
@pytest.mark.parametrize('task_type,source',[('caption','descriptions'),('classification','labels'),('vector','descriptions'),('vector','labels')])
@pytest.mark.parametrize('dataset_format,freeze',[('portable',False),('swift_sft',False),('swift_sft',True)])
@pytest.mark.parametrize('approved_only',[True,False])
async def test_pending_scope_never_exports_rejected_annotations(client,auth_headers,test_project,test_db,test_engine,task_type,source,dataset_format,approved_only,freeze):
    from models import Asset,Annotation
    from models.project_work import MatchQuery
    from services.storage import get_storage
    async with test_engine.begin() as conn:
        await conn.run_sync(lambda connection:MatchQuery.__table__.create(connection,checkfirst=True))
    test_project.task_type=task_type;test_project.label_schema={'labels':['car']}
    for status,color in [('approved','white'),('pending','blue'),('rejected','red')]:
        stream=BytesIO();Image.new('RGB',(8,8),color).save(stream,format='PNG');raw=stream.getvalue()
        path=f'isolated/{status}.png';await get_storage().save_file(BytesIO(raw),path)
        asset=Asset(project_id=test_project.id,original_name=f'{status}.png',blob_path=path,
            content_hash=hashlib.sha256(raw).hexdigest(),file_size=len(raw),format='PNG',width=8,height=8,
            source_type='upload',annotation_status='completed')
        test_db.add(asset);await test_db.flush()
        test_db.add(Annotation(asset_id=asset.id,version=1,is_latest=True,review_status=status,
            reviewer_id=1 if status!='pending' else None,annotation_data={'description':status+' description','labels':['car']}))
    await test_db.commit()
    root=f'/api/export/projects/{test_project.id}'
    if freeze:
        response=client.post(root+'/annotation-snapshots',headers=auth_headers,
            json=dict(dataset_format=dataset_format,sample_source=source,approved_only=approved_only))
        assert response.status_code==201,response.text
        manifest=response.json()['manifest']
        response=client.get('/api/export/snapshots/'+response.json()['id']+'/download',headers=auth_headers)
    else:
        response=client.get(root+'/export',headers=auth_headers,
            params=dict(format='annotation',dataset_format=dataset_format,sample_source=source,approved_only=approved_only))
    assert response.status_code==200,response.text
    with zipfile.ZipFile(BytesIO(response.content)) as package:
        manifest=json.loads(package.read('manifest.json'))
        expected={'approved'} if approved_only else {'approved','pending'}
        assert {row['review_status'] for row in manifest['assets']}==expected
        if dataset_format!='portable':
            assert manifest['training']['samples']==len(expected)
            if source=='descriptions':assert 'rejected description' not in package.read('train.jsonl').decode()


@pytest.mark.parametrize('status,approved_only,needs_review,expected',[
    ('approved',True,False,True),('pending',True,False,False),('pending',False,False,True),
    ('pending',False,True,False),('rejected',False,False,False),('rejected',True,False,False),
])
def test_match_pending_scope_keeps_special_ai_review_rules(status,approved_only,needs_review,expected):
    from services.annotation_export import exportable_match
    hit=dict(judgment='correct',review_status=status,ai_review={'status':'needs_review' if needs_review else 'agreed'})
    assert exportable_match(hit,approved_only) is expected


@pytest.mark.parametrize('mode',['ratio','train_only','val_only'])
def test_small_or_single_split_freeze_remains_legal_export(client,auth_headers,test_project,mode):
    from tests.test_p2_exports import populate
    from services.training_config import validate_snapshot
    populate(client,auth_headers,test_project.id)
    root=f'/api/export/projects/{test_project.id}'
    selection=dict(mode=mode,ratios=dict(train=80,val=10,test=10),budgets={},check_duplicates=False,
        group_similar=False,phash_distance=4,color_distance=20,require_source=False)
    body=dict(labels=['人'],approved_only=True,selection=selection)
    preview=client.post(root+'/splits/preview',headers=auth_headers,json=body)
    assert preview.status_code==200,preview.text
    assert preview.json()['can_freeze'] is True
    assert preview.json()['images_by_split']==({'val':2} if mode=='val_only' else {'train':2})
    frozen=client.post(root+'/snapshots',headers=auth_headers,json={**body,'format':'yolo'})
    assert frozen.status_code==201,frozen.text
    with pytest.raises(HTTPException,match='train和val'):
        validate_snapshot('yolo26','yolo',frozen.json()['manifest'])


@pytest.mark.parametrize('format', ['yolo','coco'])
def test_detection_freeze_allows_pending_and_excludes_rejected(client,auth_headers,test_project,format):
    from tests.test_p2_exports import populate
    from services.training_config import validate_snapshot
    ids = populate(client,auth_headers,test_project.id)
    revised = client.put('/api/annotations/'+ids[0][1]['id'],headers=auth_headers,
        json={'base_version':1,'annotation_data':ids[0][1]['annotation_data']})
    assert revised.status_code==200,revised.text
    data=BytesIO();Image.new('RGB',(100,50),'green').save(data,format='PNG')
    uploaded=client.post('/api/assets/upload',headers=auth_headers,params={'project_id':str(test_project.id)},
        files={'file':('rejected.png',data.getvalue(),'image/png')})
    assert uploaded.status_code==201,uploaded.text
    rejected_id=uploaded.json()['asset_id']
    annotation=client.post('/api/annotations',headers=auth_headers,
        json={'asset_id':rejected_id,'annotation_data':{'boxes':[]}})
    assert annotation.status_code==201,annotation.text
    rejected=client.post('/api/annotations/'+annotation.json()['id']+'/review',headers=auth_headers,
        json={'approved':False,'comment':'不通过'})
    assert rejected.status_code==200,rejected.text
    root=f'/api/export/projects/{test_project.id}'
    selection=dict(mode='ratio',ratios=dict(train=50,val=50,test=0))
    for approved_only, expected in [(False,{'approved':1,'pending':1}),(True,{'approved':1})]:
        preview=client.post(root+'/splits/preview',headers=auth_headers,
            json=dict(labels=['人'],approved_only=approved_only,selection=selection))
        assert preview.status_code==200,preview.text
        assert preview.json()['review_counts']==expected
        assert rejected_id in preview.json()['excluded_asset_ids']
    frozen=client.post(root+'/snapshots',headers=auth_headers,
        json=dict(format=format,labels=['人'],approved_only=False,selection=selection))
    assert frozen.status_code==201,frozen.text
    archive=client.get('/api/export/snapshots/'+frozen.json()['id']+'/download',headers=auth_headers)
    assert archive.status_code==200,archive.text
    with zipfile.ZipFile(BytesIO(archive.content)) as package:
        manifest=json.loads(package.read('manifest.json'))
        assert {row['asset_id'] for row in manifest['assets']}=={asset_id for asset_id,_ in ids}
        assert {row['review_status'] for row in manifest['assets']}=={'approved','pending'}
        assert all(rejected_id not in name for name in package.namelist())
        validate_snapshot('rfdetr_medium',format,manifest)
