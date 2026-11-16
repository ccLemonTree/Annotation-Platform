import hashlib
import json
from uuid import UUID
import pytest
from models import ImageUpload, Project, Tenant, User
from services.auth import create_access_token


@pytest.mark.asyncio
async def test_upload_history_is_shared_only_within_target_project(client, test_db, test_project, auth_headers):
    other = Project(tenant_id=1, project_name='same collection, other project', task_type='detection')
    test_db.add(Tenant(id=2, tenant_name='external'))
    await test_db.flush()
    test_db.add_all([other, User(id=2, tenant_id=1, username='peer', email='peer@test.local',
        password_hash='fixture', role='admin', is_active=True),
        User(id=3, tenant_id=2, username='external', email='external@test.local',
             password_hash='fixture', role='admin', is_active=True)])
    await test_db.commit()
    peer, external = [{'Authorization': 'Bearer ' + create_access_token({'user_id': user_id})} for user_id in (2, 3)]
    url = '/api/image-uploads'
    data = b'fixture'
    body = dict(filename='same.jpg', size=len(data), chunk_hashes=[hashlib.sha256(data).hexdigest()], collection_name='shared')
    project_id, other_id = str(test_project.id), str(other.id)
    def start(id=None):
        response = client.post(url, headers=auth_headers, json={**body, 'project_id': id})
        assert response.status_code == 200, response.text
        return response.json()['id']
    own_id, other_upload, library = start(project_id), start(other_id), start()
    assert len({own_id, other_upload, library}) == 3
    assert start(project_id) == own_id
    assert client.put(url+f'/{own_id}/parts/0', headers=auth_headers, content=data).status_code == 200
    def history(headers, id=None):
        response = client.get(url, headers=headers, params={'project_id':id} if id else {})
        assert response.status_code == 200, response.text
        return response.json()
    assert [(r['id'],r['can_manage']) for r in history(auth_headers, project_id)] == [(own_id,True)]
    seen = history(peer, project_id)
    assert [(r['id'],r['can_manage'],r['uploaded_bytes']) for r in seen] == [(own_id,False,len(data))]
    assert [r['id'] for r in history(auth_headers, other_id)] == [other_upload]
    assert [r['id'] for r in history(auth_headers)] == [library]
    assert not history(peer)
    assert client.get(url, headers=external, params={'project_id':project_id}).status_code == 404
    assert client.post(url, headers=external, json={**body,'project_id':project_id}).status_code == 404
    assert client.delete(url+'/'+own_id, headers=peer).status_code == 404
    assert client.put(url+f'/{own_id}/parts/0', headers=peer, content=data).status_code == 404


@pytest.mark.asyncio
async def test_reselect_legacy_file_keeps_its_parts(client, test_db, test_project, auth_headers):
    data = b'old fixture'
    hashes = [hashlib.sha256(data).hexdigest()]
    group = client.post('/api/images/collections', headers=auth_headers, json={'name':'legacy'}).json()
    identity = ['old.jpg', len(data), hashes, group['id'], 720]
    job = ImageUpload(tenant_id=1, user_id=1, collection_id=UUID(group['id']),
        filename='old.jpg', size=len(data), chunk_hashes=hashes, min_long_edge=720,
        resume_key=hashlib.sha256(json.dumps(identity,separators=(',',':')).encode()).hexdigest())
    test_db.add(job)
    await test_db.commit()
    url = '/api/image-uploads'
    assert client.put(url+f'/{job.id}/parts/0', headers=auth_headers, content=data).status_code == 200
    body = dict(filename='old.jpg', size=len(data), chunk_hashes=hashes, collection_name='legacy',
                min_long_edge=720, project_id=str(test_project.id))
    restored = client.post(url, headers=auth_headers, json=body)
    assert restored.status_code == 200, restored.text
    assert restored.json()['id'] == str(job.id)
    assert restored.json()['uploaded_parts'] == [0]
    assert restored.json()['project_id'] == str(test_project.id)
