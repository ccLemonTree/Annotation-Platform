import pytest
from tests.test_project_reuse import reusable


@pytest.mark.asyncio
async def test_device_id_and_name_search_share_listing_and_selection(client,auth_headers,reusable,test_db):
    project,_,images,assets,_=reusable
    images[0].device_info={'sources':[
        {'platform_name':'平台一','device_name':'门口','device_id':'D1793526752826'},
        {'platform_name':'平台二','device_name':'仓库','device_id':'D200'}]}
    images[2].device_info={'platform_name':'平台二','device_name':'门口','device_id':'D999'}
    for index in (0,2):assets[index].device_info=images[index].device_info
    await test_db.commit()
    for platform,query,index in [(None,'D1793526752826',0),('平台一','d1793526752826',0),
        ('平台一','352675',0),('平台二','D1793526752826',None),('平台二','D200',0),
        ('平台二','D999',2),('平台二','门口',2),('平台二','%',None),('平台二','_',None)]:
        params={'device_name':query,'page_size':1}
        if platform is not None:params['platform_name']=platform
        for path,rows,key in [('/api/images',images,'image_ids'),('/api/assets',assets,'asset_ids')]:
            filters={**params,**({'project_id':str(project.id)} if path.endswith('assets') else {})}
            listing=client.get(path,headers=auth_headers,params=filters)
            selection=client.get(path+'/selection',headers=auth_headers,params=filters)
            assert listing.status_code==selection.status_code==200
            expected=[] if index is None else [str(rows[index].id)]
            assert [item['id'] for item in listing.json()['items']]==expected,(platform,query,path)
            assert selection.json()[key]==expected,(platform,query,path)
            assert listing.json()['total']==selection.json()['total']==len(expected)


@pytest.mark.asyncio
async def test_device_id_without_platform_can_be_searched_as_local_source(client,auth_headers,reusable,test_db):
    _,_,images,_,_=reusable
    images[0].device_info={'device_id':'D1793526752826','device_name':'门口'}
    await test_db.commit()
    response=client.get('/api/images',headers=auth_headers,params={'platform_name':'@upload','device_name':'d1793526752826'})
    assert response.status_code==200
    assert [item['id'] for item in response.json()['items']]==[str(images[0].id)]
