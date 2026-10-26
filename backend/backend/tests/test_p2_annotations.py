import pytest

BOX = {'x': 4, 'y': 5, 'width': 10, 'height': 12, 'label': '人'}

def save(client, headers, asset, boxes=None, **extra):
    return client.post('/api/annotations', headers=headers, json={
        'asset_id': str(asset.id), 'annotation_data': {'boxes': [BOX] if boxes is None else boxes}, **extra})

@pytest.mark.parametrize('box', [
    {**BOX, 'x': -1}, {**BOX, 'width': 0}, {**BOX, 'x': 60},
    {**BOX, 'label': ''}, {**BOX, 'height': 'NaN'}, {**BOX, 'x': True},
])
def test_invalid_box_rejected(client, auth_headers, test_asset, box):
    assert save(client, auth_headers, test_asset, [box]).status_code in (400, 422)
    assert client.get('/api/annotations', headers=auth_headers).json()['total'] == 0

def test_versions_conflicts_history_review_and_counts(client, auth_headers, test_asset):
    first = save(client, auth_headers, test_asset).json()
    assert first['review_status'] == 'pending'
    def stats():
        return client.get(f'/api/projects/{test_asset.project_id}/stats', headers=auth_headers).json()
    assert stats()['annotated_count'] == 1
    path = '/api/annotations/' + first['id']
    assert client.post(path + '/review', headers=auth_headers, json={'approved': True}).status_code == 200
    assert stats()['reviewed_count'] == 1
    second = client.put(path, headers=auth_headers, json={
        'base_version': 1, 'annotation_data': {'boxes': [{**BOX, 'x': 8}]}})
    assert second.status_code == 200, second.text
    second = second.json()
    assert second['id'] != first['id'] and second['version'] == 2
    assert second['review_status'] == 'pending' and second['reviewer_id'] is None
    assert stats()['annotated_count'] == 1 and stats()['reviewed_count'] == 0
    dashboard = client.get('/api/stats/dashboard', headers=auth_headers).json()['annotations_overview']
    assert dashboard['total'] == 1 and dashboard['approved'] == 0
    assert client.get(path, headers=auth_headers).json()['annotation_data']['boxes'][0]['x'] == 4
    assert client.get(path, headers=auth_headers).json()['is_latest'] is False
    assert client.put(path, headers=auth_headers, json={
        'base_version': 1, 'annotation_data': {'boxes': []}}).status_code == 409
    assert client.post(path + '/review', headers=auth_headers, json={'approved': True}).status_code == 409
    assert save(client, auth_headers, test_asset).status_code == 409
    history = client.get('/api/annotations', headers=auth_headers,
        params={'asset_id': str(test_asset.id), 'latest_only': False}).json()
    assert history['total'] == 2
    assert client.get('/api/annotations', headers=auth_headers).json()['total'] == 1
    assert client.post('/api/batch/annotations/approve', headers=auth_headers, json=[first['id']]).status_code == 409
    assert client.post('/api/batch/annotations/approve', headers=auth_headers, json=[second['id']]).status_code == 200
    assert stats()['reviewed_count'] == 1
    assert client.post('/api/batch/annotations/reject', headers=auth_headers, json=[second['id']]).status_code == 200
    assert stats()['reviewed_count'] == 0
    assert client.delete('/api/assets/' + str(test_asset.id), headers=auth_headers).status_code == 204
    assert stats()['annotated_count'] == 0 and stats()['reviewed_count'] == 0

def test_negative_sample_and_audit(client, auth_headers, test_asset):
    result = save(client, auth_headers, test_asset, [])
    assert result.status_code == 201
    assert result.json()['review_status'] == 'pending'
    assert result.json()['created_at'].endswith('Z')
    records = client.get(f'/api/annotations/assets/{test_asset.id}/events', headers=auth_headers)
    assert records.status_code == 200
    assert records.json()[0]['action'] == 'create' and records.json()[0]['user_id'] == 1
    assert records.json()[0]['created_at'].endswith('+00:00') or records.json()[0]['created_at'].endswith('Z')

def test_missing_base_version_cannot_overwrite(client, auth_headers, test_asset):
    first = save(client, auth_headers, test_asset).json()
    result = client.put('/api/annotations/' + first['id'], headers=auth_headers,
        json={'annotation_data': {'boxes': []}})
    assert result.status_code in (409, 422)

def test_deletion_does_not_reuse_version_and_manual_status_cannot_fake_review(client, auth_headers, test_asset):
    first = save(client, auth_headers, test_asset).json()
    path = '/api/annotations/' + first['id']
    result = client.post('/api/batch/assets/update-status', headers=auth_headers,
        params={'new_status':'reviewed'}, json=[str(test_asset.id)])
    assert result.status_code == 409
    assert client.delete(path, headers=auth_headers).status_code == 204
    assert save(client, auth_headers, test_asset).json()['version'] == 2
