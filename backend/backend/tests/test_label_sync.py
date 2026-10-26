from tests.test_p2_annotations import BOX, save

def project(client, headers, asset):
    return client.get('/api/projects/' + str(asset.project_id), headers=headers).json()

def rename(client, headers, asset, labels, mapping, base=None):
    original = project(client, headers, asset)['label_schema'] if base is None else base
    return client.put('/api/projects/' + str(asset.project_id), headers=headers, json={
        'label_schema': {'labels': labels}, 'base_label_schema': original, 'label_renames': mapping})

def test_rename_versions_latest_boxes_and_resets_review(client, auth_headers, test_asset):
    first = save(client, auth_headers, test_asset).json()
    client.post('/api/annotations/' + first['id'] + '/review', headers=auth_headers, json={'approved': True})
    result = rename(client, auth_headers, test_asset, ['person'], {'人':'person'})
    assert result.status_code == 200, result.text
    latest = client.get('/api/annotations', headers=auth_headers, params={'asset_id':str(test_asset.id)}).json()['items'][0]
    assert latest['version'] == 2 and latest['review_status'] == 'pending'
    assert latest['annotation_data']['boxes'] == [{**BOX,'label':'person'}]
    assert client.get('/api/annotations/' + first['id'], headers=auth_headers).json()['annotation_data']['boxes'][0]['label'] == '人'
    stats = client.get('/api/projects/' + str(test_asset.project_id) + '/stats',headers=auth_headers).json()
    assert stats['annotated_count'] == 1 and stats['reviewed_count'] == 0
    assert client.put('/api/annotations/' + first['id'], headers=auth_headers, json={'annotation_data':{'boxes':[BOX]},'base_version':1}).status_code == 409

def test_used_class_removal_without_mapping_is_atomic(client,auth_headers,test_asset):
    first = save(client,auth_headers,test_asset).json()
    result = rename(client,auth_headers,test_asset,['车'],{})
    assert result.status_code == 400
    assert project(client,auth_headers,test_asset)['label_schema'] is None
    assert client.get('/api/annotations',headers=auth_headers).json()['items'][0]['id'] == first['id']

def test_invalid_and_stale_mapping_do_not_modify(client,auth_headers,test_asset):
    save(client,auth_headers,test_asset)
    assert rename(client,auth_headers,test_asset,['person'],{'人':'猫'}).status_code == 400
    assert rename(client,auth_headers,test_asset,['person'],{'未知':'person'}).status_code == 400
    assert rename(client,auth_headers,test_asset,['person'],{'人':'person'},base={'labels':['旧配置']}).status_code == 409
    assert project(client,auth_headers,test_asset)['label_schema'] is None

def test_add_reorder_do_not_rename_and_swap_is_simultaneous(client,auth_headers,test_asset):
    first = save(client,auth_headers,test_asset,[BOX,{**BOX,'label':'车','x':20}]).json()
    assert rename(client,auth_headers,test_asset,['人','车'],{}).status_code == 200
    assert rename(client,auth_headers,test_asset,['车','人'],{}).status_code == 200
    assert client.get('/api/annotations',headers=auth_headers).json()['items'][0]['id'] == first['id']
    assert rename(client,auth_headers,test_asset,['车','人'],{'人':'车','车':'人'}).status_code == 200
    latest = client.get('/api/annotations',headers=auth_headers).json()['items'][0]
    assert [b['label'] for b in latest['annotation_data']['boxes']] == ['车','人']

def test_label_sync_keeps_frozen_snapshot(client,auth_headers,test_asset,mock_image_data):
    data=client.post('/api/assets/upload',headers=auth_headers,params={'project_id':str(test_asset.project_id)},
        files={'file':('real.jpg',mock_image_data.getvalue(),'image/jpeg')}).json()
    asset=type('Asset',(),{'id':data['asset_id'],'project_id':test_asset.project_id})()
    annotation=save(client,auth_headers,asset).json()
    client.post('/api/annotations/'+annotation['id']+'/review',headers=auth_headers,json={'approved':True})
    frozen=client.post('/api/export/projects/'+str(test_asset.project_id)+'/snapshots',headers=auth_headers,
        json={'format':'yolo','approved_only':True,'splits':{data['asset_id']:'train'}}).json()
    path='/api/export/snapshots/'+frozen['id']+'/download'
    before=client.get(path,headers=auth_headers).content
    assert rename(client,auth_headers,asset,['person'],{'人':'person'}).status_code==200
    assert client.get(path,headers=auth_headers).content==before
