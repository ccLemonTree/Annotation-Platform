import hashlib
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker
from tests.test_resumable_upload import archive


@pytest.mark.parametrize('content,expected',[
    ('1 0.102083 0.156481 0.180208 0.314815',dict(label='smoke',x=.011979,y=0,width=.180208,height=.3138885)),
    ('0 .045 .5 .1 .2',dict(label='fire',x=0,y=.4,width=.095,height=.2)),
    ('0 .955 .955 .1 .1',dict(label='fire',x=.905,y=.905,width=.095,height=.095)),
])
def test_small_yolo_edge_overshoot_is_clipped(content,expected):
    from services.yolo_import import parse_boxes
    box,=parse_boxes(content,['fire','smoke'])
    assert box.pop('label')==expected.pop('label')
    assert box==pytest.approx(expected)


@pytest.mark.asyncio
async def test_edge_overshoot_import_keeps_class_and_review_policy(client,auth_headers,test_db,test_engine,mock_image_data):
    from services.image_uploads import process_one
    from models import LibraryImage
    from sqlalchemy import select
    data=archive('zip',[('data.yaml',b'names: [fire, smoke, star]\n'),('classes.txt',b'fire\nsmoke\nstar\n'),
        ('images/a.jpg',mock_image_data.getvalue()),('labels/a.txt',b'1 0.102083 0.156481 0.180208 0.314815\n')])
    url=queue(client,auth_headers,data)
    assert await process_one(async_sessionmaker(test_engine,expire_on_commit=False))
    job=client.get(url,headers=auth_headers).json()
    assert job['status']=='completed' and job['result']['annotated']==1,job
    async with async_sessionmaker(test_engine,expire_on_commit=False)() as db:
        image=await db.scalar(select(LibraryImage))
        source=image.imported_annotation;box,=source['boxes']
        assert source['names']==['fire','smoke','star'] and source['trusted'] is False
        assert box['label']=='smoke' and box['y']==0
        assert box['height']==pytest.approx(.3138885*image.height)


def queue(client,headers,data,trust=False):
    created=client.post('/api/image-uploads',headers=headers,json=dict(filename='annotated.zip',size=len(data),
        chunk_hashes=[hashlib.sha256(data).hexdigest()],collection_name='已标注图片',min_long_edge=0,annotation_format='yolo',trust_annotations=trust))
    assert created.status_code==200,created.text
    job=created.json();url='/api/image-uploads/'+job['id']
    assert client.put(url+'/parts/0',headers=headers,content=data).status_code==200
    assert client.post(url+'/complete',headers=headers).status_code==200
    return url


def attach(client,headers,project):
    image=client.get('/api/images',headers=headers).json()['items'][0]
    result=client.post('/api/images/associate',headers=headers,json=dict(project_id=str(project),image_ids=[image['id']],include_imported_annotations=True))
    assert result.status_code==200,result.text
    return image,result.json()


@pytest.mark.asyncio
async def test_library_source_is_saved_without_project_and_copied_on_request(client,auth_headers,test_project,test_db,test_engine,mock_image_data):
    from services.image_uploads import process_one
    test_project.label_schema={'labels':['fire']};await test_db.commit()
    data=archive('zip',[('classes.txt',b'fire'),('a.jpg',mock_image_data.getvalue()),('a.txt',b'0 .5 .5 .5 .5')])
    url=queue(client,auth_headers,data)
    await process_one(async_sessionmaker(test_engine,expire_on_commit=False))
    assert client.get(url,headers=auth_headers).json()['result']['annotated']==1
    assert client.get('/api/annotations',headers=auth_headers).json()['total']==0
    image=client.get('/api/images',headers=auth_headers).json()['items'][0]
    assert image['has_imported_annotation']
    plain=client.post('/api/images/associate',headers=auth_headers,json=dict(project_id=str(test_project.id),image_ids=[image['id']]))
    assert plain.status_code==200 and client.get('/api/annotations',headers=auth_headers).json()['total']==0
    response=client.post('/api/images/associate',headers=auth_headers,json=dict(project_id=str(test_project.id),image_ids=[image['id']],include_imported_annotations=True))
    assert response.status_code==200 and response.json()['annotated']==1,response.text
    from models import Project,LibraryImage
    from uuid import UUID
    other=Project(tenant_id=1,project_name='other',task_type='detection',label_schema={'labels':['fire']});test_db.add(other);await test_db.commit()
    attach(client,auth_headers,other.id)
    first=client.get('/api/annotations',headers=auth_headers,params={'project_id':str(test_project.id)}).json()['items'][0]
    assert client.put('/api/annotations/'+first['id'],headers=auth_headers,json=dict(base_version=1,annotation_data={'boxes':[]})).status_code==200
    second=client.get('/api/annotations',headers=auth_headers,params={'project_id':str(other.id)}).json()['items'][0]
    assert second['annotation_data']['boxes'][0]['label']=='fire'
    async with async_sessionmaker(test_engine,expire_on_commit=False)() as db:
        original=await db.get(LibraryImage,UUID(image['id']));assert original.imported_annotation['boxes']==second['annotation_data']['boxes']
    assert attach(client,auth_headers,test_project.id)[1]['conflicts']==1


@pytest.mark.asyncio
async def test_yolo_import_preserves_class_order_geometry_and_pending_review(client,auth_headers,test_project,test_db,test_engine,mock_image_data):
    from services.image_uploads import process_one
    test_project.label_schema={'labels':['smoke','fire']};await test_db.commit()
    data=archive('zip',[('data.yaml',b'names: [fire, smoke]\n'),('images/train/a.jpg',mock_image_data.getvalue()),('labels/train/a.txt',b'0 0.5 0.5 0.5 0.5\n')])
    url=queue(client,auth_headers,data)
    await process_one(async_sessionmaker(test_engine,expire_on_commit=False))
    result=client.get(url,headers=auth_headers).json()
    assert result['status']=='completed',result
    assert result['result']['annotated']==1
    attach(client,auth_headers,test_project.id)
    annotations=client.get('/api/annotations',params={'project_id':str(test_project.id)},headers=auth_headers).json()['items']
    assert len(annotations)==1 and annotations[0]['annotation_source']=='import_yolo'
    assert annotations[0]['annotation_data']['boxes']==[dict(label='fire',x=16.0,y=12.0,width=32.0,height=24.0)]
    assert annotations[0]['review_status']=='pending' and annotations[0]['reviewer_id'] is None
    assert client.get('/api/images',headers=auth_headers).json()['total']==1
    assert client.get('/api/projects/'+str(test_project.id),headers=auth_headers).json()['annotated_count']==1


@pytest.mark.asyncio
@pytest.mark.parametrize('label', [None,b'2 .5 .5 .1 .1',b'0 nan .5 .1 .1',b'0 .5 .5 1.5 .5',b'0 .5 .5 .1 .1 .7',b'0 .01 .01 .9 .9',b'0 -.001 .5 .1 .1',b'0 .5 .5 0 .1',b'0 .039 .5 .1 .1'])
async def test_bad_or_missing_yolo_annotation_skipped_before_image_writes(client,auth_headers,test_project,test_db,test_engine,mock_image_data,label):
    from services.image_uploads import process_one
    test_project.label_schema={'labels':['fire']};await test_db.commit()
    rows=[('classes.txt',b'fire\n'),('images/a.jpg',mock_image_data.getvalue())]
    if label is not None:rows.append(('labels/a.txt',label))
    url=queue(client,auth_headers,archive('zip',rows))
    await process_one(async_sessionmaker(test_engine,expire_on_commit=False))
    result=client.get(url,headers=auth_headers).json()
    assert result['status']=='completed' and result['result']['invalid']==1
    assert result['result']['errors']
    assert client.get('/api/images',headers=auth_headers).json()['total']==0


@pytest.mark.asyncio
async def test_trusted_import_can_be_used_as_validation_truth_without_extra_review(client,auth_headers,test_project,test_db,test_engine,mock_image_data):
    from services.image_uploads import process_one
    from models.validation import ValidationSet,ValidationRun,ValidationItem
    async with test_engine.begin() as db:
        await db.run_sync(lambda conn:ValidationSet.__table__.create(conn,checkfirst=True))
        await db.run_sync(lambda conn:ValidationRun.__table__.create(conn,checkfirst=True))
        await db.run_sync(lambda conn:ValidationItem.__table__.create(conn,checkfirst=True))
    test_project.label_schema={'labels':['fire']};await test_db.commit()
    data=archive('zip',[('classes.txt',b'fire'),('a.jpg',mock_image_data.getvalue()),('a.txt',b'')])
    url=queue(client,auth_headers,data,True)
    await process_one(async_sessionmaker(test_engine,expire_on_commit=False))
    result=client.get(url,headers=auth_headers).json();assert result['status']=='completed',result
    attach(client,auth_headers,test_project.id)
    annotation=client.get('/api/annotations',headers=auth_headers).json()['items'][0]
    assert annotation['review_status']=='approved' and annotation['reviewer_id']==1
    response=client.post('/api/validation/sets',headers=auth_headers,json=dict(name='信任导入真值',project_id=str(test_project.id)))
    assert response.status_code==201,response.text
    assert response.json()['total']==1
    exported=client.get('/api/export/projects/'+str(test_project.id)+'/export',headers=auth_headers,params={'format':'yolo'})
    assert exported.status_code==200,exported.text[:100]
    events=client.get('/api/annotations/assets/'+annotation['asset_id']+'/events',headers=auth_headers).json()
    assert any(event['action']=='approve' and event['detail']['source']=='import_yolo' for event in events)


@pytest.mark.asyncio
@pytest.mark.parametrize('label',[b'0 .5 .5 .1 .1',b''])
async def test_unused_archive_classes_do_not_block_project_import(client,auth_headers,test_project,test_db,test_engine,mock_image_data,label):
    from services.image_uploads import process_one
    test_project.label_schema={'labels':['fire','smoke','star']};await test_db.commit()
    data=archive('zip',[('classes.txt',b'fire\nsmoke\nhotwork\n'),('a.jpg',mock_image_data.getvalue()),('a.txt',label)])
    queue(client,auth_headers,data,True)
    await process_one(async_sessionmaker(test_engine,expire_on_commit=False))
    _,result=attach(client,auth_headers,test_project.id)
    assert result['annotated']==1
    await test_db.refresh(test_project)
    assert test_project.label_schema=={'labels':['fire','smoke','star']}
    annotation=client.get('/api/annotations',headers=auth_headers).json()['items'][0]
    assert annotation['review_status']=='approved'
    assert len(annotation['annotation_data']['boxes'])==bool(label)


@pytest.mark.asyncio
async def test_actual_unknown_box_class_still_rejected_without_partial_project_writes(client,auth_headers,test_project,test_db,test_engine,mock_image_data):
    from services.image_uploads import process_one
    test_project.label_schema={'labels':['fire','smoke','star']};await test_db.commit()
    data=archive('zip',[('classes.txt',b'fire\nsmoke\nhotwork\n'),('a.jpg',mock_image_data.getvalue()),('a.txt',b'2 .5 .5 .1 .1')])
    queue(client,auth_headers,data)
    await process_one(async_sessionmaker(test_engine,expire_on_commit=False))
    image=client.get('/api/images',headers=auth_headers).json()['items'][0]
    response=client.post('/api/images/associate',headers=auth_headers,json=dict(project_id=str(test_project.id),image_ids=[image['id']],include_imported_annotations=True))
    assert response.status_code==400 and 'hotwork' in response.json()['detail']
    assert client.get('/api/assets',headers=auth_headers,params={'project_id':str(test_project.id)}).json()['total']==0
    assert client.get('/api/annotations',headers=auth_headers).json()['total']==0


@pytest.mark.asyncio
async def test_trust_import_requires_admin(client,auth_headers,test_project,test_user,test_db):
    test_user.role='annotator';await test_db.commit()
    response=client.post('/api/image-uploads',headers=auth_headers,json=dict(filename='a.zip',size=1,chunk_hashes=['a'*64],collection_name='a',annotation_format='yolo',trust_annotations=True))
    assert response.status_code==403,response.text


@pytest.mark.parametrize('rows',[
    [('data.yaml',b'names: [fire]'),('classes.txt',b'smoke'),('a.jpg',b'image'),('a.txt',b'')],
    [('classes.txt',b'fire'),('../a.jpg',b'image'),('a.txt',b'')],
    [('classes.txt',b'fire'),('a.jpg',b'image'),('a.txt',b''),('a.txt',b'0 .5 .5 .1 .1')],
])
def test_yolo_archive_rejects_ambiguous_classes_and_unsafe_paths(tmp_path,rows):
    from services.yolo_import import import_items
    path=tmp_path/'annotated.zip';path.write_bytes(archive('zip',rows))
    with pytest.raises(ValueError):list(import_items(path,'annotated.zip',0))


def test_shared_label_skips_both_ambiguous_images_and_resume_keeps_later_image(tmp_path):
    from services.yolo_import import import_items
    path=tmp_path/'shared.zip'
    path.write_bytes(archive('zip',[('classes.txt',b'fire'),('images/a.jpg',b'image'),('images/a.png',b'image'),
        ('labels/a.txt',b''),('images/good.jpg',b'image'),('labels/good.txt',b'')]))
    items=list(import_items(path,'shared.zip',0))
    assert [item[3] for item in items]==['invalid','invalid',None]
    assert all('同一TXT' in item[5] for item in items[:2])
    assert list(import_items(path,'shared.zip',2))==items[2:]


@pytest.mark.asyncio
async def test_bad_yolo_entries_do_not_block_later_valid_images(client,auth_headers,mock_image_data,test_engine):
    from services.image_uploads import process_one
    rows=[('classes.txt',b'fire'),('images/missing.jpg',mock_image_data.getvalue()),
        ('images/bad-label.jpg',mock_image_data.getvalue()),('labels/bad-label.txt',b'5 .5 .5 .1 .1'),
        ('images/broken.jpg',b'broken JPEG'),('labels/broken.txt',b''),
        ('images/good.jpg',mock_image_data.getvalue()),('labels/good.txt',b'0 .5 .5 .5 .5'),('labels/orphan.txt',b'')]
    url=queue(client,auth_headers,archive('zip',rows))
    await process_one(async_sessionmaker(test_engine,expire_on_commit=False))
    receipt=client.get(url,headers=auth_headers).json();result=receipt['result']
    assert receipt['status']=='completed',receipt
    assert (result['imported'],result['annotated'],result['invalid'],result['ignored'])==(1,1,3,1)
    assert len(result['errors'])==4
    assert client.get('/api/images',headers=auth_headers).json()['total']==1


@pytest.mark.asyncio
@pytest.mark.parametrize('yolo',[True,False])
async def test_bad_member_crc_does_not_block_other_images(client,auth_headers,mock_image_data,test_engine,yolo):
    from services.image_uploads import process_one
    from tests.test_resumable_upload import start
    marker=b'damaged member'
    data=archive('zip',[('classes.txt',b'fire'),('images/broken.jpg',marker),('labels/broken.txt',b''),
        ('images/good.jpg',mock_image_data.getvalue()),('labels/good.txt',b'0 .5 .5 .1 .1')])
    assert data.count(marker)==1
    data=data.replace(marker,b'Damaged member',1)
    if yolo:url=queue(client,auth_headers,data)
    else:
        job,parts=start(client,auth_headers,data);url='/api/image-uploads/'+job['id']
        for i,part in enumerate(parts):assert client.put(url+'/parts/'+str(i),headers=auth_headers,content=part).status_code==200
        assert client.post(url+'/complete',headers=auth_headers).status_code==200
    await process_one(async_sessionmaker(test_engine,expire_on_commit=False))
    receipt=client.get(url,headers=auth_headers).json();result=receipt['result']
    assert receipt['status']=='completed' and result['imported']==1 and result['invalid']==1,receipt
    assert any('broken.jpg' in error for error in result['errors'])


@pytest.mark.asyncio
async def test_import_explicit_negative_and_conflicts_do_not_overwrite(client,auth_headers,test_project,test_db,test_engine,mock_image_data):
    from services.image_uploads import process_one
    test_project.label_schema={'labels':['fire']};await test_db.commit()
    async def run(label):
        url=queue(client,auth_headers,archive('zip',[('classes.txt',b'fire'),('a.jpg',mock_image_data.getvalue()),('a.txt',label)]))
        await process_one(async_sessionmaker(test_engine,expire_on_commit=False))
        return client.get(url,headers=auth_headers).json()
    negative=await run(b'')
    assert negative['result']['annotated']==1
    conflicting=await run(b'0 .5 .5 .5 .5')
    assert conflicting['result']['conflicts']==1
    attach(client,auth_headers,test_project.id)
    annotations=client.get('/api/annotations',headers=auth_headers).json()['items']
    assert len(annotations)==1 and annotations[0]['annotation_data']['boxes']==[]


@pytest.mark.asyncio
async def test_reusing_imported_annotation_requires_scoped_detection_project(client,auth_headers,test_project,test_db,test_engine,mock_image_data):
    from services.image_uploads import process_one
    from models import Tenant,Project
    test_db.add(Tenant(id=2,tenant_name='other'));await test_db.flush()
    other=Project(tenant_id=2,project_name='other',task_type='detection',label_schema={'labels':['fire']});test_db.add(other);await test_db.commit()
    data=archive('zip',[('classes.txt',b'fire'),('a.jpg',mock_image_data.getvalue()),('a.txt',b'')])
    url=queue(client,auth_headers,data)
    await process_one(async_sessionmaker(test_engine,expire_on_commit=False))
    assert client.get(url,headers=auth_headers).json()['status']=='completed'
    image=client.get('/api/images',headers=auth_headers).json()['items'][0]
    payload=dict(project_id=str(other.id),image_ids=[image['id']],include_imported_annotations=True)
    assert client.post('/api/images/associate',headers=auth_headers,json=payload).status_code==404
    test_project.task_type='classification';await test_db.commit();payload['project_id']=str(test_project.id)
    assert client.post('/api/images/associate',headers=auth_headers,json=payload).status_code==400
