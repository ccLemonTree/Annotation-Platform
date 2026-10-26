from tests.test_image_library import foreign_image

def upload(client,headers,image,name):
    return client.post('/api/images/batch-upload',headers=headers,data={'collection_name':name},
        files={'files':('collection.jpg',image.getvalue(),'image/jpeg')})

def test_named_upload_keeps_duplicate_in_multiple_collections(client,auth_headers,mock_image_data):
    first=upload(client,auth_headers,mock_image_data,' 1013 ')
    assert first.status_code==201,first.text
    second=upload(client,auth_headers,mock_image_data,'1014')
    assert second.status_code==201,second.text
    assert first.json()[0]['id']==second.json()[0]['id']
    groups=client.get('/api/images/collections',headers=auth_headers)
    assert groups.status_code==200,groups.text
    groups={group['name']:group for group in groups.json()}
    assert set(groups)=={'1013','1014'}
    for group in groups.values():
        assert group['image_count']==1
        result=client.get('/api/images',headers=auth_headers,params={'collection_id':group['id']}).json()
        assert result['total']==1 and set(result['items'][0]['collection_names'])=={'1013','1014'}
    assert client.get('/api/images',headers=auth_headers).json()['total']==1

def test_assign_existing_image_preserves_project_annotation(client,auth_headers,mock_image_data,test_project):
    from tests.test_p2_annotations import BOX
    image=upload(client,auth_headers,mock_image_data,'1013').json()[0]
    association=client.post('/api/images/associate',headers=auth_headers,json={'project_id':str(test_project.id),'image_ids':[image['id']]}).json()
    asset_id=association['asset_ids'][0]
    annotation=client.post('/api/annotations',headers=auth_headers,json={'asset_id':asset_id,'base_version':0,'annotation_data':{'boxes':[BOX]}}).json()
    payload={'name':'手工核对','image_ids':[image['id'],image['id']]}
    response=client.post('/api/images/collections/assign',headers=auth_headers,json=payload)
    assert response.status_code==200,response.text
    assert response.json()['added_count']==1
    assert client.post('/api/images/collections/assign',headers=auth_headers,json=payload).json()['added_count']==0
    assert client.get('/api/annotations/'+annotation['id'],headers=auth_headers).json()['annotation_data']==annotation['annotation_data']
    assert client.get('/api/images/'+image['id']+'/content',headers=auth_headers).content==mock_image_data.getvalue()

def test_collection_name_validation_and_scope(client,auth_headers,mock_image_data,foreign_image):
    for name in ('   ','a'*201):
        assert upload(client,auth_headers,mock_image_data,name).status_code==422
    assert client.get('/api/images/collections').status_code==401
    assert client.post('/api/images/collections/assign',headers=auth_headers,
        json={'name':'其他租户','image_ids':[str(foreign_image.id)]}).status_code==404
    assert client.get('/api/images/collections',headers=auth_headers).json()==[]
