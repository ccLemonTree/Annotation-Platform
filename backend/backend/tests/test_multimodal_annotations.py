import pytest
from sqlalchemy import select
from models import Project, Annotation


@pytest.mark.asyncio
async def test_classification_contract_versions_and_review(client, auth_headers, test_asset, test_db):
    project = await test_db.get(Project, test_asset.project_id)
    project.task_type = 'classification'
    project.label_schema = {'labels': ['口罩', '无口罩'], 'classification_mode': 'single'}
    await test_db.commit()
    def save(data, version=0):
        return client.post('/api/annotations', headers=auth_headers, json={
            'asset_id': str(test_asset.id), 'annotation_data': data, 'base_version': version})
    for data in [{'labels': ['口罩', '无口罩']}, {'labels': ['unknown']}, {'labels': []}, {'labels': ['口罩', '口罩']}]:
        assert save(data).status_code == 400
    first = save({'labels': ['口罩'], 'description': '佩戴黑色口罩'})
    assert first.status_code == 201, first.text
    assert save({'labels': ['无口罩']}).status_code == 409
    assert client.post('/api/annotations/' + first.json()['id'] + '/review', headers=auth_headers,
        json={'approved': True}).status_code == 200
    project.label_schema = {**project.label_schema, 'classification_mode': 'multi'}
    await test_db.commit()
    assert save({'labels': ['口罩', '无口罩']}, 1).status_code == 201


@pytest.mark.parametrize('task', ['caption', 'vector'])
@pytest.mark.asyncio
async def test_description_and_vector_validate_shared_data(client, auth_headers, test_asset, test_db, task):
    project = await test_db.get(Project, test_asset.project_id)
    project.task_type = task
    project.label_schema = {'labels': ['黑衣'], 'classification_mode': 'multi'}
    await test_db.commit()
    path = '/api/annotations'
    def save(data):
        return client.post(path, headers=auth_headers, json={'asset_id': str(test_asset.id), 'annotation_data': data})
    assert save({'description': ' ', 'labels': []}).status_code == 400
    assert save({'description': '黑衣男子', 'labels': ['其他']}).status_code == 400
    response = save({'description': '黑衣男子', 'labels': ['黑衣']})
    assert response.status_code == 201, response.text
    assert response.json()['annotation_data']['description'] == '黑衣男子'


def test_new_task_types_and_project_mode_validation():
    from schemas.project import ProjectCreate
    for task in ['caption', 'vector']:
        assert ProjectCreate(project_name='test', task_type=task).task_type == task
    with pytest.raises(ValueError):
        ProjectCreate(project_name='test', task_type='classification', label_schema={'classification_mode': 'invalid'})


@pytest.mark.asyncio
async def test_classification_schema_cannot_orphan_labels(client,auth_headers,test_asset,test_db):
    project=await test_db.get(Project,test_asset.project_id)
    project.task_type='classification';project.label_schema={'labels':['旧类别'],'classification_mode':'single'}
    await test_db.commit()
    saved=client.post('/api/annotations',headers=auth_headers,json={'asset_id':str(test_asset.id),'annotation_data':{'labels':['旧类别']}})
    assert saved.status_code==201
    path=f'/api/projects/{project.id}'
    response=client.put(path,headers=auth_headers,json={'label_schema':{'labels':['新类别']},'base_label_schema':project.label_schema})
    assert response.status_code==400
    response=client.put(path,headers=auth_headers,json={'label_schema':{'labels':['新类别']},'base_label_schema':project.label_schema,'label_renames':{'旧类别':'新类别'}})
    assert response.status_code==200,response.text
    latest=client.get('/api/annotations',headers=auth_headers).json()['items'][0]
    assert latest['annotation_data']['labels']==['新类别'] and latest['version']==2


@pytest.mark.asyncio
async def test_default_caption_export_images_and_source(client,auth_headers,test_asset,test_db,tmp_path):
    import hashlib,json,zipfile
    from io import BytesIO
    from PIL import Image
    from services.storage import get_storage
    project=await test_db.get(Project,test_asset.project_id);project.task_type='caption'
    raw=BytesIO();Image.new('RGB',(64,48),'red').save(raw,'PNG');content=raw.getvalue()
    test_asset.content_hash=hashlib.sha256(content).hexdigest();test_asset.file_size=len(content);test_asset.format='PNG'
    test_asset.device_info={'parent_image_id':str(test_asset.id)}
    await test_db.commit()
    await get_storage().save_file(BytesIO(content),test_asset.blob_path)
    saved=client.post('/api/annotations',headers=auth_headers,json={'asset_id':str(test_asset.id),'annotation_data':{'description':'人佩戴口罩'}}).json()
    path=f'/api/export/projects/{project.id}/export?format=annotation'
    assert client.get(path,headers=auth_headers).status_code==400
    client.post('/api/annotations/'+saved['id']+'/review',headers=auth_headers,json={'approved':True})
    response=client.get(path,headers=auth_headers)
    assert response.status_code==200,response.text
    with zipfile.ZipFile(BytesIO(response.content)) as archive:
        row=json.loads(archive.read('annotations.jsonl').decode().splitlines()[0])
        assert row['description']=='人佩戴口罩' and row['source']['parent_image_id']==str(test_asset.id)
        assert row['image'] in archive.namelist()
