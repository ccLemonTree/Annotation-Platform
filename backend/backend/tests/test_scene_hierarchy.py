import pytest
from pydantic import ValidationError
from schemas.image_ingest import ImageIngestConfig


def test_groups_keep_existing_labels_and_legacy_config():
    config=ImageIngestConfig(scenes=['工厂','河边','自定义旧场景'])
    assert config.scenes==['工厂','河边','自定义旧场景']
    groups={g.name:g.scenes for g in config.scene_groups}
    assert groups['生产作业']==['工厂'] and groups['自然环境']==['河边']
    assert groups['其他场景']==['自定义旧场景']


def test_parent_filter_includes_historical_broad_labels():
    from services.scene_categories import scene_filter_values
    config=ImageIngestConfig(scenes=['道路','路口'])
    assert set(scene_filter_values('@group:交通场景',[config]))=={'交通场景','道路','路口'}


@pytest.mark.asyncio
async def test_empty_image_result_is_displayed_and_filtered_as_unmatched(client,auth_headers,test_db,test_user,test_project):
    from models import LibraryImage,Asset,Annotation
    image=LibraryImage(tenant_id=test_user.tenant_id,blob_path='empty.jpg',original_name='empty.jpg',content_hash='empty-scene',file_size=1,source_type='upload',scene_status='completed',scene_labels=[])
    test_db.add(image);await test_db.flush()
    asset=Asset(project_id=test_project.id,image_id=image.id,blob_path=image.blob_path,original_name=image.original_name,content_hash=image.content_hash,file_size=1,source_type='upload')
    test_db.add(asset);await test_db.flush()
    test_db.add(Annotation(asset_id=asset.id,annotation_data={'boxes':[]},version=1,is_latest=True,review_status='approved'))
    await test_db.commit()
    result=client.get('/api/images',headers=auth_headers,params={'scene_status':'unmatched'}).json()
    assert result['total']==1 and result['items'][0]['scene_status']=='unmatched'
    assert client.get('/api/images',headers=auth_headers,params={'scene_status':'completed'}).json()['total']==0
    project=client.get('/api/assets',headers=auth_headers,params={'project_id':str(test_project.id)}).json()
    assert project['items'][0]['scene_status']=='unmatched'


@pytest.mark.parametrize('groups',[
    [{'name':' ','scenes':['道路']}],
    [{'name':'交通','scenes':['道路']},{'name':'交通','scenes':['路口']}],
    [{'name':'交通','scenes':['道路']},{'name':'公共','scenes':['道路']}],
])
def test_invalid_groups_are_rejected(groups):
    with pytest.raises(ValidationError):ImageIngestConfig(scenes=['道路','路口'],scene_groups=groups)


@pytest.mark.asyncio
async def test_group_filter_covers_images_projects_selection_and_tenant_scope(client,auth_headers,test_db,test_user,test_project):
    from models import LibraryImage,Asset,Annotation,Tenant,ImageCollection
    config={'scenes':['道路','路口','河边','电梯'],'scene_groups':[
        {'name':'交通场景','scenes':['道路','路口']},{'name':'自然环境','scenes':['河边']},{'name':'室内空间','scenes':['电梯']}]}
    saved=client.put('/api/images/ingest-settings',headers=auth_headers,json=config)
    assert saved.status_code==200,saved.text
    ids=[]
    for label in config['scenes']:
        image=LibraryImage(tenant_id=test_user.tenant_id,blob_path=label+'.jpg',original_name=label+'.jpg',content_hash=label,
            file_size=1,source_type='upload',scene_status='completed',scene_labels=[label])
        test_db.add(image);await test_db.flush();ids.append(str(image.id))
        asset=Asset(project_id=test_project.id,image_id=image.id,blob_path=image.blob_path,original_name=image.original_name,
            content_hash=label,file_size=1,source_type='upload')
        test_db.add(asset);await test_db.flush()
        test_db.add(Annotation(asset_id=asset.id,annotation_data={'boxes':[]},version=1,is_latest=True,review_status='approved'))
    test_db.add(Tenant(id=99,tenant_name='foreign'));await test_db.flush()
    test_db.add(ImageCollection(tenant_id=99,name='foreign',ingest_config={
        'scenes':['电梯'],'scene_groups':[{'name':'交通场景','scenes':['电梯']}]}))
    await test_db.commit()
    params={'scene':'@group:交通场景','page_size':1}
    images=client.get('/api/images',headers=auth_headers,params=params)
    assert images.status_code==200,images.text
    assert images.json()['total']==2 and len(images.json()['items'])==1
    selected=client.get('/api/images/selection',headers=auth_headers,params=params).json()
    assert set(selected['image_ids'])==set(ids[:2])
    assets=client.get('/api/assets',headers=auth_headers,params={**params,'project_id':str(test_project.id)}).json()
    assert assets['total']==2 and len(assets['items'])==1
    selection=client.get('/api/assets/selection',headers=auth_headers,params={**params,'project_id':str(test_project.id)}).json()
    assert selection['total']==2
    assert client.get('/api/images',headers=auth_headers,params={'scene':'道路'}).json()['total']==1
    assert client.get('/api/images',headers=auth_headers,params={'scene':'@group:不存在'}).json()['total']==0
