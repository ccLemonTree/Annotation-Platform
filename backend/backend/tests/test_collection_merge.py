import hashlib,json
from uuid import uuid4
from datetime import datetime
import pytest
from fastapi import HTTPException
from sqlalchemy import select,func
from models import ImageCollection,ImageCollectionMember,LibraryImage,ImageUpload,ImageUploadItem

def resume_key(name,group):
    return hashlib.sha256(json.dumps([name,1,[hashlib.sha256(b'x').hexdigest()],str(group),720],separators=(',',':')).encode()).hexdigest()

async def fixture(db,user):
    source=ImageCollection(tenant_id=user.tenant_id,name='source',ingest_config={},ingest_revision=1)
    target=ImageCollection(tenant_id=user.tenant_id,name='target',ingest_config={},ingest_revision=2)
    db.add_all([source,target]);await db.flush()
    images=[]
    for i in range(3):
        image=LibraryImage(tenant_id=user.tenant_id,blob_path=f'{i}.jpg',content_hash=str(i),original_name=f'{i}.jpg',
            file_size=1,source_type='upload',scene_status='pending' if i==1 else 'completed',scene_labels=[] if i==1 else ['工厂'],
            scene_config={'collection_id':str(source.id),'revision':1,'config':{'classification_enabled':True,'scenes':['工厂']}} if i<2 else None,
            scene_metadata={'model':'preserve-me'})
        db.add(image);await db.flush();images.append(image)
    for group,indexes in ((source,[0,1]),(target,[0,2])):
        for i in indexes:db.add(ImageCollectionMember(collection_id=group.id,image_id=images[i].id))
    job=ImageUpload(tenant_id=user.tenant_id,user_id=user.id,collection_id=source.id,filename='archive.zip',size=1,
        chunk_hashes=[hashlib.sha256(b'x').hexdigest()],resume_key=resume_key('archive.zip',source.id),min_long_edge=720,
        status='completed',unique_count=2,created_at=datetime(2026,10,15))
    old=ImageUpload(tenant_id=user.tenant_id,user_id=user.id,collection_id=target.id,filename='archive.zip',size=1,
        chunk_hashes=job.chunk_hashes,resume_key=resume_key('archive.zip',target.id),min_long_edge=720,
        status='completed',unique_count=1,created_at=datetime(2026,10,14))
    db.add_all([job,old]);await db.flush()
    for image in images[:2]:db.add(ImageUploadItem(upload_id=job.id,image_id=image.id))
    db.add(ImageUploadItem(upload_id=old.id,image_id=images[0].id));await db.commit()
    return source,target,images,job

@pytest.mark.asyncio
async def test_merge_preserves_union_scenes_and_resumable_receipts(client,auth_headers,test_db,test_user):
    from services.image_library import merge_collections
    source,target,images,job=await fixture(test_db,test_user)
    source_id=source.id;target_id=target.id;job_id=job.id
    await merge_collections(test_db,test_user.tenant_id,source.id,target.id);await test_db.commit()
    test_db.expire_all()
    assert await test_db.get(ImageCollection,source_id) is None
    members=(await test_db.scalars(select(ImageCollectionMember.image_id).where(ImageCollectionMember.collection_id==target_id))).all()
    assert len(members)==3
    saved=(await test_db.scalars(select(LibraryImage).order_by(LibraryImage.original_name))).all()
    assert len(saved)==3 and saved[0].scene_labels==['工厂'] and saved[0].scene_metadata=={'model':'preserve-me'}
    assert saved[1].scene_status=='pending' and saved[1].scene_config['collection_id']==str(target_id) and saved[1].scene_config['revision']==2
    await test_db.commit()
    result=client.post('/api/image-uploads',headers=auth_headers,json={'filename':'archive.zip','size':1,'chunk_hashes':[hashlib.sha256(b'x').hexdigest()],'collection_name':'target','min_long_edge':720})
    assert result.status_code==200 and result.json()['id']==str(job_id) and result.json()['status']=='completed'
    assert await test_db.scalar(select(func.count()).select_from(ImageUploadItem))==3

@pytest.mark.asyncio
@pytest.mark.parametrize('blocked',['upload','inference','tenant'])
async def test_merge_rejects_active_work_and_foreign_scope(test_db,test_user,blocked):
    from services.image_library import merge_collections
    source,target,images,job=await fixture(test_db,test_user)
    if blocked=='upload':job.status='uploading';job.resume_key=uuid4().hex
    if blocked=='inference':images[1].scene_status='processing'
    await test_db.commit()
    with pytest.raises(HTTPException) as caught:
        await merge_collections(test_db,999 if blocked=='tenant' else test_user.tenant_id,source.id,target.id)
    assert caught.value.status_code==(404 if blocked=='tenant' else 409)
    await test_db.rollback()
    assert await test_db.scalar(select(func.count()).select_from(ImageCollection))==2
    assert await test_db.scalar(select(func.count()).select_from(ImageCollectionMember))==4
