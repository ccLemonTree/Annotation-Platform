"""Project reuse shares originals while keeping annotation versions independent."""
from datetime import datetime
from uuid import UUID,uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select

from models import Annotation,Asset,LibraryImage,Project,Tenant,TaskType
from tests.test_p2_annotations import BOX


@pytest_asyncio.fixture
async def analytics_activity_table(test_engine):
    from models.work_session import WorkActivityInterval
    async with test_engine.begin() as conn:
        await conn.run_sync(WorkActivityInterval.__table__.create,checkfirst=True)


@pytest_asyncio.fixture
async def reusable(test_db,test_project,test_user):
    test_project.label_schema={'labels':['人','车']}
    source=Project(tenant_id=test_user.tenant_id,project_name='source',task_type=TaskType.DETECTION,
        label_schema={'labels':['人','车']},total_assets=3,annotated_count=3,reviewed_count=2)
    other=Project(tenant_id=test_user.tenant_id,project_name='other source',task_type=TaskType.DETECTION)
    test_db.add_all([source,other]);await test_db.flush()
    images=[];assets=[];annotations=[]
    for index,(label,scene,status) in enumerate([('人','工厂','approved'),('车','河边','pending'),('人','工厂','approved')]):
        image=LibraryImage(tenant_id=test_user.tenant_id,blob_path=f'shared-{index}.jpg',content_hash=f'reuse-{index}',
            original_name=f'reuse-{index}.jpg',file_size=10,width=64,height=48,format='JPEG',source_type='upload',
            scene_labels=[scene],scene_status='completed')
        test_db.add(image);await test_db.flush()
        asset=Asset(project_id=source.id,image_id=image.id,blob_path=image.blob_path,content_hash=image.content_hash,
            original_name=image.original_name,file_size=10,width=64,height=48,format='JPEG',source_type='upload',annotation_status='reviewed')
        test_db.add(asset);await test_db.flush()
        annotation=Annotation(asset_id=asset.id,version=2,is_latest=True,annotation_data={'boxes':[{**BOX,'label':label}]},
            annotation_source='manual',annotator_id=test_user.id,review_status=status,reviewer_id=test_user.id if status=='approved' else None,
            reviewed_at=datetime(2026,10,20) if status=='approved' else None,review_comment='来源人工审核' if status=='approved' else None)
        test_db.add(annotation)
        images.append(image);assets.append(asset);annotations.append(annotation)
    other_asset=Asset(project_id=other.id,image_id=images[0].id,blob_path=images[0].blob_path,content_hash=images[0].content_hash,
        original_name='duplicate.jpg',file_size=10,width=64,height=48,source_type='upload')
    test_db.add(other_asset);await test_db.flush()
    test_db.add(Annotation(asset_id=other_asset.id,version=1,is_latest=True,annotation_data={'boxes':[{**BOX,'label':'车'}]},review_status='approved'))
    test_db.add(Annotation(asset_id=assets[1].id,version=1,is_latest=False,annotation_data={'boxes':[BOX]},review_status='approved'))
    await test_db.commit()
    return source,other,images,assets,annotations


def test_source_label_scene_and_approval_filters_share_listing_selection(client,auth_headers,reusable):
    source,_,images,_,_=reusable
    params={'source_project_id':str(source.id),'label':'人','scene':'工厂','review_status':'approved'}
    listing=client.get('/api/images',headers=auth_headers,params=params)
    selection=client.get('/api/images/selection',headers=auth_headers,params=params)
    assert listing.status_code==selection.status_code==200
    assert listing.json()['total']==selection.json()['total']==2
    assert set(selection.json()['image_ids'])=={str(images[0].id),str(images[2].id)}
    params['label']='车'
    assert client.get('/api/images/selection',headers=auth_headers,params=params).json()['total']==0
    assert client.get('/api/images/selection',headers=auth_headers,params={
        'source_project_id':str(source.id),'label':'人','scene':'河边','review_status':'approved'}).json()['total']==0


def reuse(client,headers,target,source,images,**changes):
    return client.post('/api/images/associate',headers=headers,json={
        'project_id':str(target),'source_project_id':str(source),'image_ids':[str(image.id) for image in images],
        'include_source_annotations':True,**changes})

@pytest.mark.asyncio
async def test_workflow_crop_can_be_reused_with_pending_attributes(client,auth_headers,reusable,test_project,test_db,mock_image_data):
    from io import BytesIO
    from services.storage import get_storage

    source,other,_,_,_=reusable
    source.task_type=test_project.task_type=TaskType.CAPTION
    source.label_schema={'annotation_scope':'crops'}
    test_project.label_schema={'annotation_scope':'crops'}
    content=mock_image_data.getvalue()
    asset=Asset(project_id=source.id,blob_path='project-crops/vehicle.jpg',content_hash='workflow-vehicle',
        original_name='workflow-vehicle.jpg',file_size=len(content),width=64,height=48,format='JPEG',
        source_type='model_crop',device_info={'parent_image_id':str(uuid4()),'crop':{'box':{'label':'car'}}})
    test_db.add(asset)
    await test_db.flush()
    data={'attribute_schema':{'target_label':'car','fields':[{'key':'body_color','name':'车身颜色'}]},
          'attributes':{'body_color':{'value':'白色','reason':''}}}
    test_db.add(Annotation(asset_id=asset.id,version=1,is_latest=True,review_status='pending',annotation_data=data))
    await test_db.commit()
    await get_storage().save_file(BytesIO(content),asset.blob_path)

    filters={'source_project_id':str(source.id),'review_status':'approved_or_pending','source_type':'model_crop'}
    listing=client.get('/api/images',headers=auth_headers,params=filters)
    assert listing.status_code==200 and listing.json()['total']==1
    assert listing.json()['items'][0]['id']==str(asset.id)
    assert listing.json()['items'][0]['asset_id']==str(asset.id)
    selection=client.get('/api/images/selection',headers=auth_headers,params=filters).json()
    assert selection['image_ids']==[str(asset.id)]
    assert reuse(client,auth_headers,test_project.id,other.id,[asset]).status_code==404
    result=reuse(client,auth_headers,test_project.id,source.id,[asset],include_pending_source_annotations=True)
    assert result.status_code==200,result.text
    assert result.json()['reused_annotations']==1
    copy_id=result.json()['asset_ids'][0]
    assert copy_id!=str(asset.id)
    copied=(await test_db.execute(select(Asset,Annotation).join(Annotation,Annotation.asset_id==Asset.id)
        .where(Asset.id==UUID(copy_id)))).one()
    assert copied.Asset.blob_path==asset.blob_path and copied.Asset.device_info==asset.device_info
    assert copied.Asset.source_type=='model_crop'
    assert copied.Annotation.annotation_data==data and copied.Annotation.status_for('image')=='pending'
    repeated=reuse(client,auth_headers,test_project.id,source.id,[asset],include_pending_source_annotations=True)
    assert repeated.json()['conflicts']==1 and repeated.json()['added_count']==0
    excluded=client.get('/api/images/selection',headers=auth_headers,params=filters|{'exclude_project_id':str(test_project.id)})
    assert excluded.json()['image_ids']==[]
    source_asset_id=asset.id
    await test_db.commit()
    assert client.delete(f'/api/assets/{source_asset_id}',headers=auth_headers).status_code==204
    assert client.get(f'/api/assets/{copy_id}/content',headers=auth_headers).content==content


@pytest.mark.asyncio
async def test_annotated_reuse_filters_both_crop_sources_and_skipped_rows(client,auth_headers,reusable,test_project,test_db):
    source,_,_,assets,annotations=reusable
    source.task_type=test_project.task_type=TaskType.CAPTION
    source.label_schema={'annotation_scope':'crops'}
    for index,asset in enumerate(assets):
        asset.source_type='model_crop'
        if index==0:
            asset.device_info={'uploaded_crop':True}
        else:
            asset.image_id=None
        annotations[index].annotation_data={'description':'白色车辆'} if index<2 else {'skipped':True}
    await test_db.commit()
    filters={'source_project_id':str(source.id),'review_status':'approved_or_pending',
             'reuse_project_id':str(test_project.id)}
    listing=client.get('/api/images',headers=auth_headers,params=filters)
    assert listing.status_code==200
    assert listing.json()['total']==2
    assert {row['id'] for row in listing.json()['items']}=={str(assets[0].image_id),str(assets[1].id)}
    selection=client.get('/api/images/selection',headers=auth_headers,params=filters).json()
    assert set(selection['image_ids'])=={str(assets[0].image_id),str(assets[1].id)}
    result=reuse(client,auth_headers,test_project.id,source.id,assets[1:2],include_pending_source_annotations=True)
    assert result.status_code==200 and result.json()['reused_annotations']==1
    refreshed=client.get('/api/images',headers=auth_headers,params=filters|{'page_size':1}).json()
    assert refreshed['total']==2 and refreshed['items'][0]['in_target_project']==(refreshed['items'][0]['id']==str(assets[1].id))
    all_rows=client.get('/api/images',headers=auth_headers,params=filters).json()['items']
    assert [row['id'] for row in all_rows if row['in_target_project']]==[str(assets[1].id)]

@pytest.mark.asyncio
async def test_uploaded_crop_is_listed_and_reused_as_crop(client,auth_headers,reusable,test_project,test_db):
    source,_,images,assets,_=reusable
    source.label_schema={'annotation_scope':'crops','labels':['车']}
    assets[1].source_type='model_crop'
    assets[1].device_info={'uploaded_crop':True}
    await test_db.commit()
    filters={'source_project_id':str(source.id),'source_type':'upload','q':images[1].original_name}
    assert client.get('/api/images',headers=auth_headers,params=filters).json()['total']==1
    assert client.get('/api/images/selection',headers=auth_headers,params=filters).json()['image_ids']==[str(images[1].id)]
    response=reuse(client,auth_headers,test_project.id,source.id,images[1:2],include_source_annotations=False)
    assert response.status_code==200,response.text
    asset=client.get('/api/assets/'+response.json()['asset_ids'][0],headers=auth_headers).json()
    assert asset['source_type']=='model_crop' and asset['image_id']==str(images[1].id)
    assert client.get('/api/annotations',headers=auth_headers,params={'asset_id':asset['id']}).json()['total']==0


@pytest.mark.asyncio
async def test_pending_attribute_copy_is_opt_in_and_stays_pending(client,auth_headers,reusable,test_project,test_db):
    source,_,images,assets,annotations=reusable
    source.task_type=test_project.task_type=TaskType.CAPTION
    assets[1].source_type='model_crop'
    assets[1].device_info={'uploaded_crop':True}
    definition={'target_label':['car'],'fields':[{'key':'color','name':'颜色','instruction':'按图判断','options':[]}]}
    data={'attribute_schema':definition,'attributes':{'color':{'value':'白色','reason':'可见'}}}
    annotations[0].annotation_data={'description':'已审核车辆描述'}
    annotations[1].annotation_data=data
    annotations[2].review_status='rejected'
    await test_db.commit()
    filters={'source_project_id':str(source.id),'review_status':'approved_or_pending'}
    assert set(client.get('/api/images/selection',headers=auth_headers,params=filters).json()['image_ids'])=={str(images[0].id),str(images[1].id)}
    assert client.get('/api/images',headers=auth_headers,params=filters).json()['total']==2
    approved_only=reuse(client,auth_headers,test_project.id,source.id,images[1:2])
    assert approved_only.json()['reused_annotations']==0
    response=reuse(client,auth_headers,test_project.id,source.id,images[1:],include_pending_source_annotations=True)
    assert response.status_code==200,response.text
    assert response.json()['reused_annotations']==1 and response.json()['without_annotations']==1
    copies=client.get('/api/annotations',headers=auth_headers,params={'project_id':str(test_project.id)}).json()['items']
    assert len(copies)==1
    assert copies[0]['annotation_data']==data and copies[0]['review_status']=='pending'
    assert copies[0]['reviewer_id'] is None
    repeated=reuse(client,auth_headers,test_project.id,source.id,images[1:2],include_pending_source_annotations=True)
    assert repeated.json()['conflicts']==1


def test_reuse_copies_only_latest_approved_into_independent_target_and_keeps_provenance(
        client,auth_headers,reusable,test_project,analytics_activity_table):
    source,_,images,_,annotations=reusable
    source_summary=client.get('/api/analytics',headers=auth_headers,params={'project_id':str(source.id)})
    assert source_summary.status_code==200,source_summary.text
    response=reuse(client,auth_headers,test_project.id,source.id,images[:1])
    assert response.status_code==200,response.text
    result=response.json()
    assert result['added_count']==1 and result['reused_annotations']==result['annotated']==1
    assert result['without_annotations']==0
    summary=client.get('/api/analytics',headers=auth_headers,params={'project_id':str(test_project.id)})
    assert summary.status_code==200,summary.text
    assert summary.json()['quality']['sources']['reuse']==1
    assert summary.json()['workload']['human_annotation_events']==summary.json()['workload']['human_review_events']==0
    assert summary.json()['workload']['automatic_annotation_events']==summary.json()['workload']['automatic_review_events']==0
    assert client.get('/api/analytics',headers=auth_headers,params={'project_id':str(source.id)}).json()==source_summary.json()
    assets=client.get('/api/assets',headers=auth_headers,params={'project_id':str(test_project.id)}).json()['items']
    copied_asset=next(asset for asset in assets if asset['image_id']==str(images[0].id))
    assert copied_asset['blob_path']==images[0].blob_path and copied_asset['scene_labels']==['工厂']
    copied=client.get('/api/annotations',headers=auth_headers,params={'asset_id':copied_asset['id']}).json()['items'][0]
    assert copied['id']!=str(annotations[0].id) and copied['version']==1
    assert copied['review_status']=='approved' and copied['annotation_source']=='reuse' and copied['reviewer_id'] is None
    assert datetime.fromisoformat(copied['reviewed_at']).replace(tzinfo=None)==annotations[0].reviewed_at
    events=client.get('/api/annotations/assets/'+copied_asset['id']+'/events',headers=auth_headers).json()
    evidence=next(event['detail'] for event in events if event['action']=='reuse')
    assert evidence['source_project_id']==str(source.id) and evidence['source_annotation_id']==str(annotations[0].id)
    assert evidence['source_version']==2 and evidence['source_reviewer_id']==1
    assert not any(event['action']=='approve' for event in events)
    assert client.get('/api/projects/'+str(test_project.id)+'/stats',headers=auth_headers).json()['reviewed_count']==1
    assert client.put('/api/annotations/'+copied['id'],headers=auth_headers,json={
        'base_version':1,'annotation_data':{'boxes':[{**BOX,'x':8}]}}).status_code==200
    original=client.get('/api/annotations/'+str(annotations[0].id),headers=auth_headers).json()
    assert original['annotation_data']['boxes'][0]['x']==4 and original['version']==2
    assert client.delete('/api/projects/'+str(source.id),headers=auth_headers).status_code==204
    assert client.get('/api/annotations/'+copied['id'],headers=auth_headers).status_code==200
    assert client.get('/api/images',headers=auth_headers).json()['total']==3


def test_existing_target_images_can_receive_annotations_but_existing_versions_never_overwritten(
        client,auth_headers,reusable,test_project):
    source,_,images,_,_=reusable
    payload={'project_id':str(test_project.id),'image_ids':[str(images[0].id)]}
    assert client.post('/api/images/associate',headers=auth_headers,json=payload).status_code==200
    first=reuse(client,auth_headers,test_project.id,source.id,images[:1])
    assert first.status_code==200,first.text
    assert first.json()['added_count']==0 and first.json()['reused_annotations']==1
    second=reuse(client,auth_headers,test_project.id,source.id,images[:1])
    assert second.json()['conflicts']==1 and second.json()['reused_annotations']==0
    assert client.get('/api/annotations',headers=auth_headers,params={'project_id':str(test_project.id),'latest_only':False}).json()['total']==1


@pytest.mark.asyncio
async def test_nonmatching_categories_reuse_image_without_inventing_negative_annotation(
        client,auth_headers,reusable,test_project,test_db):
    source,_,images,_,_=reusable
    test_project.label_schema={'labels':['车']};await test_db.commit()
    response=reuse(client,auth_headers,test_project.id,source.id,images[:1])
    assert response.status_code==200,response.text
    assert response.json()['reused_annotations']==0 and response.json()['annotation_skipped']==1
    assert client.get('/api/assets',headers=auth_headers,params={'project_id':str(test_project.id)}).json()['total']==1
    assert client.get('/api/annotations',headers=auth_headers,params={'project_id':str(test_project.id)}).json()['total']==0


def test_source_is_required_scoped_and_annotation_sources_are_exclusive(client,auth_headers,reusable,test_project):
    source,_,images,_,_=reusable
    body={'project_id':str(test_project.id),'image_ids':[str(images[0].id)],'include_source_annotations':True}
    assert client.post('/api/images/associate',headers=auth_headers,json=body).status_code==422
    assert reuse(client,auth_headers,test_project.id,source.id,images[:1],include_imported_annotations=True).status_code==422
    assert reuse(client,auth_headers,test_project.id,uuid4(),images[:1]).status_code==404
    assert client.get('/api/images/selection',headers=auth_headers,params={'source_project_id':str(uuid4())}).status_code==404


def test_project_scene_filters_match_list_selection_and_neighbors(client,auth_headers,reusable):
    source,_,images,assets,_=reusable
    params={'project_id':str(source.id),'scene':'工厂','label':'人'}
    listing=client.get('/api/assets',headers=auth_headers,params=params)
    selection=client.get('/api/assets/selection',headers=auth_headers,params=params)
    assert listing.status_code==selection.status_code==200
    assert listing.json()['total']==selection.json()['total']==2
    assert set(selection.json()['asset_ids'])=={str(assets[0].id),str(assets[2].id)}
    neighbors=client.get('/api/assets/'+str(assets[0].id)+'/neighbors',headers=auth_headers,params={'scene':'工厂','label':'人'})
    assert neighbors.status_code==200 and neighbors.json()['total']==2
    assert listing.headers['X-Asset-Scene-Filter']=='1'


@pytest.mark.parametrize('task_type,data',[
    ('classification',{'labels':['人']}),('caption',{'description':'一名工人'}),('vector',{'labels':['人'],'description':'工厂人员'})])
@pytest.mark.asyncio
async def test_reuse_uses_each_supported_task_validator(client,auth_headers,reusable,test_project,test_db,task_type,data):
    source,_,images,_,annotations=reusable
    source.task_type=TaskType(task_type);test_project.task_type=TaskType(task_type)
    annotations[0].annotation_data=data
    if task_type=='classification':
        annotations[2].annotation_data={'labels':['人']}
    await test_db.commit()
    response=reuse(client,auth_headers,test_project.id,source.id,images[:1])
    assert response.status_code==200,response.text
    assert response.json()['reused_annotations']==1
    copied=client.get('/api/annotations',headers=auth_headers,params={'project_id':str(test_project.id)}).json()['items'][0]
    assert copied['annotation_data']==data
    if task_type=='classification':
        assert client.get('/api/images/selection',headers=auth_headers,params={
            'source_project_id':str(source.id),'label':'人','review_status':'approved'}).json()['total']==2


@pytest.mark.parametrize('source_type,target_type', [('detection','classification'),('segmentation','segmentation')])
@pytest.mark.asyncio
async def test_copy_rejects_unsupported_or_mismatched_types_but_image_only_reuse_allowed(
        client,auth_headers,reusable,test_project,test_db,source_type,target_type):
    source,_,images,_,_=reusable
    source.task_type=TaskType(source_type);test_project.task_type=TaskType(target_type)
    await test_db.commit()
    response=reuse(client,auth_headers,test_project.id,source.id,images[:1])
    assert response.status_code==400,response.text
    assert client.get('/api/assets',headers=auth_headers,params={'project_id':str(test_project.id)}).json()['total']==0
    image_only=reuse(client,auth_headers,test_project.id,source.id,images[:1],include_source_annotations=False)
    assert image_only.status_code==200 and image_only.json()['added_count']==1


@pytest.mark.asyncio
async def test_foreign_source_and_source_image_membership_are_checked_before_commit(
        client,auth_headers,reusable,test_project,test_db):
    source,other,images,_,_=reusable
    test_db.add(Tenant(id=91,tenant_name='reuse foreign'));await test_db.flush()
    foreign=Project(tenant_id=91,project_name='private',task_type=TaskType.DETECTION)
    test_db.add(foreign);await test_db.commit()
    for path in ('/api/images','/api/images/selection'):
        assert client.get(path,headers=auth_headers,params={'source_project_id':str(foreign.id)}).status_code==404
    assert reuse(client,auth_headers,test_project.id,foreign.id,images[:1]).status_code==404
    assert reuse(client,auth_headers,test_project.id,other.id,images[1:2]).status_code==404
    assert reuse(client,auth_headers,source.id,source.id,images[:1]).status_code==400
    assert client.get('/api/assets',headers=auth_headers,params={'project_id':str(test_project.id)}).json()['total']==0


@pytest.mark.asyncio
async def test_any_existing_target_annotation_is_protected_even_without_latest_flag(
        client,auth_headers,reusable,test_project,test_db):
    source,_,images,_,_=reusable
    attached=client.post('/api/images/associate',headers=auth_headers,json={
        'project_id':str(test_project.id),'image_ids':[str(images[0].id)]}).json()
    existing=Annotation(asset_id=UUID(attached['asset_ids'][0]),version=1,is_latest=False,
        annotation_data={'boxes':[]},review_status='rejected')
    test_db.add(existing);await test_db.commit()
    response=reuse(client,auth_headers,test_project.id,source.id,images[:1])
    assert response.status_code==200 and response.json()['conflicts']==1 and response.json()['reused_annotations']==0
    rows=(await test_db.scalars(select(Annotation).where(Annotation.asset_id==existing.asset_id))).all()
    assert len(rows)==1 and rows[0].annotation_data=={'boxes':[]}


@pytest.mark.asyncio
async def test_partial_source_annotations_are_project_scoped_pending_copies(client,auth_headers,reusable,test_project,test_db):
    source,_,images,_,annotations=reusable
    original={'boxes':[BOX,{**BOX,'label':'车','x':20}]}
    annotations[0].annotation_data=original;test_project.label_schema={'labels':['人']};await test_db.commit()
    response=reuse(client,auth_headers,test_project.id,source.id,images[:1])
    assert response.status_code==200,response.text
    copied=client.get('/api/annotations',headers=auth_headers,params={'project_id':str(test_project.id)}).json()['items'][0]
    assert copied['annotation_data']=={'boxes':[BOX]} and copied['review_status']=='pending'
    assert client.get('/api/annotations/'+str(annotations[0].id),headers=auth_headers).json()['annotation_data']==original
    events=client.get('/api/annotations/assets/'+copied['asset_id']+'/events',headers=auth_headers).json()
    evidence=next(event['detail'] for event in events if event['action']=='reuse')
    assert evidence['source_review_status']=='approved' and evidence['filtered'] is True


@pytest.mark.parametrize('negative,extra_class',[(True,False),(False,True),(True,True)])
@pytest.mark.asyncio
async def test_source_negative_is_preserved_but_new_class_coverage_requires_review(
        client,auth_headers,reusable,test_project,test_db,negative,extra_class):
    source,_,images,_,annotations=reusable
    if negative:annotations[0].annotation_data={'boxes':[]}
    if extra_class:test_project.label_schema={'labels':['人','车','狗']}
    await test_db.commit()
    response=reuse(client,auth_headers,test_project.id,source.id,images[:1])
    assert response.status_code==200,response.text
    copied=client.get('/api/annotations',headers=auth_headers,params={'project_id':str(test_project.id)}).json()['items'][0]
    assert copied['review_status']==('pending' if extra_class else 'approved')
    assert copied['annotation_data']['boxes']==([] if negative else [BOX])


@pytest.mark.parametrize('matching',[True,False])
@pytest.mark.asyncio
async def test_classification_reuse_filters_labels_without_fabricating_empty_classification(
        client,auth_headers,reusable,test_project,test_db,matching):
    source,_,images,_,annotations=reusable
    source.task_type=test_project.task_type=TaskType.CLASSIFICATION
    source.label_schema={'labels':['人','车'],'classification_mode':'multi'}
    test_project.label_schema={'labels':['人']}
    annotations[0].annotation_data={'labels':['人','车'] if matching else ['车']}
    await test_db.commit()
    response=reuse(client,auth_headers,test_project.id,source.id,images[:1])
    assert response.status_code==200,response.text
    rows=client.get('/api/annotations',headers=auth_headers,params={'project_id':str(test_project.id)}).json()['items']
    if matching:assert rows[0]['annotation_data']=={'labels':['人']} and rows[0]['review_status']=='pending'
    else:assert rows==[] and response.json()['annotation_skipped']==1


@pytest.mark.parametrize('kind',['partial','outside','negative','historical'])
@pytest.mark.asyncio
async def test_imported_yolo_annotations_filter_labels_without_overwriting_target(
        client,auth_headers,reusable,test_project,test_db,kind):
    _,_,images,_,_=reusable
    test_project.label_schema={'labels':['人']}
    boxes=[BOX,{**BOX,'label':'车'}] if kind=='partial' else [{**BOX,'label':'车'}] if kind=='outside' else []
    images[0].imported_annotation={'format':'yolo','boxes':boxes,'names':['人','车'],'trusted':True,
        'trusted_by':1,'imported_by':1,'imported_at':'2026-11-01T00:00:00+00:00'}
    await test_db.commit()
    payload={'project_id':str(test_project.id),'image_ids':[str(images[0].id)]}
    if kind=='historical':
        attached=client.post('/api/images/associate',headers=auth_headers,json=payload).json()
        test_db.add(Annotation(asset_id=UUID(attached['asset_ids'][0]),version=1,is_latest=False,
            annotation_data={'boxes':[BOX]},review_status='rejected'))
        await test_db.commit()
    response=client.post('/api/images/associate',headers=auth_headers,json={**payload,'include_imported_annotations':True})
    assert response.status_code==200,response.text
    rows=client.get('/api/annotations',headers=auth_headers,params={'project_id':str(test_project.id),'latest_only':False}).json()['items']
    if kind=='outside':assert rows==[] and response.json()['annotation_skipped']==1
    elif kind=='historical':assert len(rows)==1 and response.json()['conflicts']==1
    else:
        assert len(rows)==1 and rows[0]['annotation_data']['boxes']==([BOX] if kind=='partial' else [])
        assert rows[0]['review_status']==('pending' if kind=='partial' else 'approved')
