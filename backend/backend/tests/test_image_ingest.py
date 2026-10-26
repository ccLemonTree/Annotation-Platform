from io import BytesIO
from PIL import Image
import pytest

def picture(width,height):
    data=BytesIO();Image.new('RGB',(width,height),'blue').save(data,format='PNG');return data.getvalue()

def test_settings_validate_and_persist(client,auth_headers):
    url='/api/images/ingest-settings'
    assert client.get(url).status_code==401
    default=client.get(url,headers=auth_headers)
    assert default.status_code==200,default.text
    config={**default.json(),'classification_enabled':True,'base_url':'http://model.test/v1','model':'Qwen3.8-27B',
        'scenes':['工厂','河边','高位摄像头'],'min_long_edge':720}
    saved=client.put(url,headers=auth_headers,json=config)
    assert saved.status_code==200,saved.text
    assert client.get(url,headers=auth_headers).json()['min_long_edge']==720
    for changes in ({'base_url':'file:///secret'},{'min_long_edge':-1},{'scenes':['   ']},{'model':''}):
        assert client.put(url,headers=auth_headers,json={**config,**changes}).status_code==422

def test_mixed_upload_accepts_inclusive_long_edge_before_storage(client,auth_headers,tmp_path):
    response=client.post('/api/images/batch-upload',headers=auth_headers,data={'min_long_edge':'720','collection_name':'场景测试'},
        files=[('files',('small.png',picture(719,719),'image/png')),('files',('landscape.png',picture(720,30),'image/png')),
               ('files',('portrait.png',picture(30,720),'image/png')),('files',('square.png',picture(720,720),'image/png'))])
    assert response.status_code==201,response.text
    assert response.headers.get('x-images-skipped')=='1'
    assert len(response.json())==3
    assert {row['original_name'] for row in response.json()}=={'landscape.png','portrait.png','square.png'}
    assert len(list((tmp_path/'storage'/'images').rglob('*.png')))==3

def test_enabled_upload_queues_deduplicated_image(client,auth_headers,mock_image_data):
    config=client.get('/api/images/ingest-settings',headers=auth_headers).json()
    client.put('/api/images/ingest-settings',headers=auth_headers,json={**config,'classification_enabled':True})
    def upload():
        response=client.post('/api/images/batch-upload',headers=auth_headers,
            files={'files':('fixture.jpg',mock_image_data.getvalue(),'image/jpeg')})
        assert response.status_code==201,response.text
        return response.json()[0]
    first,second=upload(),upload()
    assert first['id']==second['id'] and second['scene_status']=='pending'
    assert client.get('/api/images',headers=auth_headers,params={'scene_status':'pending'}).json()['total']==1

def test_legacy_batch_applies_platform_rule_without_partial_references(client,auth_headers,test_project):
    config=client.get('/api/images/ingest-settings',headers=auth_headers).json()
    client.put('/api/images/ingest-settings',headers=auth_headers,json={**config,'min_long_edge':720})
    response=client.post('/api/assets/batch-upload',headers=auth_headers,params={'project_id':str(test_project.id)},
        files=[('files',('small.png',picture(719,719),'image/png')),('files',('large.png',picture(720,10),'image/png'))])
    assert response.status_code==201,response.text
    assert len(response.json())==1 and response.headers['x-images-skipped']=='1'
    assert client.get('/api/projects/'+str(test_project.id),headers=auth_headers).json()['total_assets']==1

@pytest.mark.asyncio
async def test_ingest_settings_admin_only(client,auth_headers,test_db,test_user):
    config=client.get('/api/images/ingest-settings',headers=auth_headers).json()
    test_user.role='viewer';await test_db.commit()
    assert client.put('/api/images/ingest-settings',headers=auth_headers,json=config).status_code==403
    assert client.post('/api/images/ingest-settings/test',headers=auth_headers,json=config).status_code==403
    assert client.get('/api/images/ingest-settings',headers=auth_headers).status_code==200
