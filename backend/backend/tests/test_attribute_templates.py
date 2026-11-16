import pytest
import pytest_asyncio
from models import Base,Tenant,User
from services.auth import create_access_token


@pytest_asyncio.fixture
async def templates_table(test_engine):
    table=Base.metadata.tables.get('object_attribute_templates')
    if table is not None:
        async with test_engine.begin() as connection:
            await connection.run_sync(lambda conn:table.create(conn))


def definition():
    return {'target_label':['car','bus'],'fields':[{'key':'color','name':'颜色','instruction':'按可见颜色判断','options':['黑','白']}]}


@pytest.mark.asyncio
async def test_update_existing_and_builtin_templates(client,auth_headers,templates_table,test_db):
    url='/api/attribute-templates'
    payload={'name':'自定义','config':definition()}
    saved=client.post(url,headers=auth_headers,json=payload).json()
    revised={'name':'更新后的模板','config':{**definition(),'target_label':['truck']}}
    for template_id in (saved['id'],'builtin_vehicle'):
        response=client.put(f'{url}/{template_id}',headers=auth_headers,json=revised)
        assert response.status_code==200,response.text
        assert response.json()=={'id':template_id,**revised}
        assert response.json() in client.get(url,headers=auth_headers).json()
        revised={**revised,'name':'租户车辆模板'}
    assert client.put(f'{url}/{saved["id"]}',headers=auth_headers,json=revised).status_code==409
    assert client.put(f'{url}/builtin_unknown',headers=auth_headers,json=revised).status_code==404
    test_db.add(Tenant(id=2,tenant_name='another'));await test_db.flush()
    test_db.add(User(id=2,tenant_id=2,username='peer',email='peer@example.com',password_hash='unused',role='admin'));await test_db.commit()
    other={'Authorization':'Bearer '+create_access_token({'user_id':2})}
    assert client.get(url,headers=other).json()==[]
    assert client.put(f'{url}/{saved["id"]}',headers=other,json=revised).status_code==404
    assert client.put(f'{url}/builtin_vehicle',headers=other,json=payload).status_code==200
    assert client.get(url,headers=other).json()==[{'id':'builtin_vehicle',**payload}]
    assert next(row for row in client.get(url,headers=auth_headers).json() if row['id']=='builtin_vehicle')['name']=='租户车辆模板'
    assert client.put(f'{url}/builtin_vehicle',headers=auth_headers,json={**payload,'config':{'target_label':[],'fields':[]}}).status_code==422


@pytest.mark.asyncio
async def test_template_save_list_duplicate_and_delete(client,auth_headers,templates_table,test_db,test_project):
    before=test_project.label_schema
    response=client.post('/api/attribute-templates',headers=auth_headers,json={'name':'  人车通用  ','config':definition()})
    assert response.status_code==201,response.text
    saved=response.json();assert saved['name']=='人车通用' and saved['config']==definition()
    assert client.get('/api/attribute-templates',headers=auth_headers).json()==[saved]
    assert client.post('/api/attribute-templates',headers=auth_headers,json={'name':'人车通用','config':definition()}).status_code==409
    await test_db.refresh(test_project);assert test_project.label_schema==before;await test_db.commit()
    assert client.delete('/api/attribute-templates/'+saved['id'],headers=auth_headers).status_code==204
    assert client.get('/api/attribute-templates',headers=auth_headers).json()==[]


@pytest.mark.asyncio
async def test_templates_tenant_and_role_isolation(client,auth_headers,templates_table,test_db,test_user):
    test_db.add(Tenant(id=2,tenant_name='other'));await test_db.flush()
    test_db.add_all([User(id=2,tenant_id=1,username='member',email='member@test.com',password_hash='unused',role='user'),User(id=3,tenant_id=2,username='peer',email='peer@test.com',password_hash='unused',role='admin')]);await test_db.commit()
    def headers(id):return {'Authorization':'Bearer '+create_access_token({'user_id':id})}
    saved=client.post('/api/attribute-templates',headers=auth_headers,json={'name':'车辆','config':definition()}).json()
    assert client.get('/api/attribute-templates',headers=headers(3)).json()==[]
    assert client.delete('/api/attribute-templates/'+saved['id'],headers=headers(3)).status_code==404
    for method,path,kwargs in [('get','/api/attribute-templates',{}),('post','/api/attribute-templates',{'json':{'name':'x','config':definition()}}),('put','/api/attribute-templates/'+saved['id'],{'json':{'name':'x','config':definition()}}),('delete','/api/attribute-templates/'+saved['id'],{})]:
        assert getattr(client,method)(path,headers=headers(2),**kwargs).status_code==403
    assert client.get('/api/attribute-templates').status_code==401


@pytest.mark.parametrize('change',[{'name':' '},{'config':{'target_label':[],'fields':[]}},{'config':{'target_label':['car',' '],'fields':definition()['fields']}},{'tenant_id':2}])
def test_templates_reject_invalid_input(client,auth_headers,templates_table,change):
    response=client.post('/api/attribute-templates',headers=auth_headers,json={'name':'模板','config':definition(),**change})
    assert response.status_code==422,response.text


def test_multiple_target_labels_and_legacy_shape():
    from schemas.object_attributes import AttributeSchema
    config=AttributeSchema.model_validate(definition());assert config.target_labels==['car','bus']
    config=AttributeSchema.model_validate({**definition(),'target_label':'car'})
    assert config.target_labels==['car'] and config.model_dump()['target_label']=='car'
