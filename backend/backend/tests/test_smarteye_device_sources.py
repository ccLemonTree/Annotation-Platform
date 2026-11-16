"""Every retained device download writer passes the same normalized provenance."""
from pathlib import Path
import pytest
from PIL import Image

SOURCE={'plat_id':'p','device_id':'d','device_name':'工厂大门','platform_name':'平台'}


def test_name_only_source_uses_explicit_non_camera_identity():
    from integrations.smarteye.runtime import device_source
    source=device_source('平台',{'deviceName':' 工厂 大门 '},'p')
    assert source['device_name']=='工厂 大门' and source['identity_kind']=='name'
    assert source['device_id'].startswith('__name__:')


@pytest.mark.parametrize('writer',['main','ledger','tool','smart'])
def test_download_writers_pass_device_source(tmp_path,monkeypatch,writer):
    from integrations.smarteye import app
    from integrations.smarteye.api import ledger_image_downloader as ledger,tool_tasks as tool,smart_search as smart
    module={'main':app,'ledger':ledger,'tool':tool,'smart':smart}[writer]
    saved=[]
    class Response:
        def raise_for_status(self):pass
    class Session:
        def get(self,*args,**kwargs):return Response()
        def close(self):pass
    monkeypatch.setattr(module,'cached_image',lambda *args,**kwargs:False)
    monkeypatch.setattr(module,'response_bytes',lambda response:b'image')
    monkeypatch.setattr(module,'save_image_content',lambda *args,**kwargs:saved.append(kwargs) or 'images/a.jpg')
    monkeypatch.setattr(module.requests,'get',lambda *args,**kwargs:Response())
    if writer=='main':assert app.download_file('http://fixture/a.jpg',str(tmp_path/'a.jpg'),Session(),'平台',SOURCE)
    elif writer=='ledger':assert ledger._download_image('http://fixture/a.jpg',str(tmp_path/'a.jpg'),Session(),device_info=SOURCE)
    elif writer=='tool':
        monkeypatch.setattr(tool,'tool_tasks',{})
        assert tool.download_single_image('http://fixture/a.jpg',str(tmp_path/'a.jpg'),'task',device_info=SOURCE)
    else:
        assert smart.download_images(['http://fixture/a.jpg'],tmp_path/'smart','平台',
            [{'imageUrl':'http://fixture/a.jpg','deviceId':'d','deviceName':'工厂大门'}],'p')==1
    assert saved[0]['device_info']==SOURCE


def test_crop_keeps_original_device_source(tmp_path,monkeypatch):
    from integrations.smarteye.api import ledger_image_downloader as ledger
    source=tmp_path/'original.jpg';Image.new('RGB',(20,20),'red').save(source)
    saved=[]
    monkeypatch.setattr(ledger,'save_image_content',lambda *args,**kwargs:saved.append(kwargs) or 'images/crop.jpg')
    assert ledger.crop_image_from_bbox(str(source),{'ltx':0,'lty':0,'width':10,'height':10},str(tmp_path/'crop.jpg'),device_info=SOURCE)
    assert saved[0]['device_info']==SOURCE


def test_selected_zip_passes_each_record_device(client,auth_headers,monkeypatch):
    from integrations.smarteye import app
    from integrations.smarteye.api import platform_enum
    saved=[]
    monkeypatch.setattr(platform_enum,'get_combined_platform_mapping',lambda:{'平台':{'platId':'p'}})
    def download(url,path,session=None,collection_name=None,device_info=None):
        Path(path).write_bytes(b'fixture');saved.append(device_info);return True
    monkeypatch.setattr(app,'download_file',download)
    response=client.post('/api/smarteye/download-images',headers=auth_headers,json={
        'urls':['http://fixture/a.jpg'],'collection_name':'平台',
        'records':[{'deviceId':'d','deviceName':'工厂大门'}]})
    assert response.status_code==200,response.text
    assert saved==[SOURCE]
    assert client.post('/api/smarteye/download-images',headers=auth_headers,json={
        'urls':['http://fixture/a.jpg'],'records':[]}).status_code==400
