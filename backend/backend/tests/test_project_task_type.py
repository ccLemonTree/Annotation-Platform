import pytest
from models import Annotation, AutomationJob, Base, ExportSnapshot, MatchQuery, ProjectWorkJob, TrainingTask, ValidationSet


@pytest.fixture
async def task_type_tables(test_engine):
    async with test_engine.begin() as connection:
        await connection.run_sync(lambda conn: Base.metadata.create_all(conn, tables=[
            ProjectWorkJob.__table__, MatchQuery.__table__, TrainingTask.__table__, ValidationSet.__table__]))


@pytest.mark.usefixtures('task_type_tables')
def test_change_task_type_with_unannotated_images(client, auth_headers, test_project, test_asset):
    path = f'/api/projects/{test_project.id}'
    response = client.put(path, headers=auth_headers, json={'task_type': 'classification'})
    assert response.status_code == 200, response.text
    assert response.json()['task_type'] == 'classification'
    assert response.json()['total_assets'] == 1
    assert client.get(f'/api/assets/{test_asset.id}', headers=auth_headers).status_code == 200


@pytest.mark.parametrize('record', ['annotation', 'automation', 'work', 'snapshot', 'validation', 'match', 'training'])
@pytest.mark.usefixtures('task_type_tables')
async def test_related_data_prevents_type_change(client, test_db, auth_headers, test_project, test_asset, record):
    project_id = test_project.id
    items = {
        'annotation': Annotation(asset_id=test_asset.id, annotation_data={'boxes': []}, is_latest=False),
        'automation': AutomationJob(project_id=project_id, tenant_id=1, user_id=1, kind='preannotate',
                                    config={}, models={}, label_schema={}, total=0),
        'work': ProjectWorkJob(project_id=project_id, user_id=1, operation='crop', model={}, options={}),
        'snapshot': ExportSnapshot(project_id=project_id, user_id=1, export_format='yolo',
                                   blob_path='fixture.zip', content_hash='fixture', manifest={}),
        'validation': ValidationSet(project_id=project_id, tenant_id=1, user_id=1, name='fixture',
                                    labels=[], samples=[], source={}),
        'match': MatchQuery(project_id=project_id, query='fixture', space={}, hits=[]),
        'training': TrainingTask(project_id=project_id, task_name='fixture', framework='pytorch',
                                 model_type='yolo', config_yaml='', dataset_version='fixture'),
    }
    test_db.add(items[record])
    await test_db.commit()
    path = f'/api/projects/{project_id}'
    response = client.put(path, headers=auth_headers, json={'task_type': 'caption', 'project_name': 'should not save'})
    assert response.status_code == 409, response.text
    unchanged = client.get(path, headers=auth_headers).json()
    assert unchanged['task_type'] == 'detection' and unchanged['project_name'] == test_project.project_name
    assert client.put(path, headers=auth_headers, json={'project_name': 'renamed'}).status_code == 200
