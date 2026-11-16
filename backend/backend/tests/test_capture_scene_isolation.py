from io import BytesIO

import pytest
from PIL import Image
from sqlalchemy import select

from tests.test_device_scenes import scenes


def capture(scenes,monkeypatch,*,status='completed',source='auto',representative=True,task='capture'):
    from integrations.smarteye import runtime,device_capture
    monkeypatch.setattr(device_capture,'_platform_tokens',{})
    monkeypatch.setattr(device_capture,'_platform_token_locks',{})
    from integrations.smarteye.management import update_device,get_device
    from integrations.smarteye.api import ledger_image_downloader,smart_search
    from models import Base,ImageCollection,ImageCollectionMember,ImageIngestSettings
    from schemas.image_ingest import ImageIngestConfig
    module,_,_,_=scenes;r=runtime.get_runtime()
    Base.metadata.create_all(r.engine,tables=[ImageCollection.__table__,ImageCollectionMember.__table__])
    with r.engine.begin() as conn:
        conn.execute(ImageIngestSettings.__table__.insert().values(tenant_id=1,revision=2,
            config=ImageIngestConfig(classification_enabled=True).model_dump()))
    raw=BytesIO();Image.new('RGB',(32,24),'red').save(raw,'PNG')
    old=BytesIO();Image.new('RGB',(32,24),'blue').save(old,'PNG');(r.root/'old.png').write_bytes(old.getvalue())
    state={'device_name':'工厂大门','platform_name':'平台','scene_source':source,'scene_status':status,
        'scene_labels':['河边'] if status!='unclassified' else [],'scene_revision':5,
        'scene_input':['工厂大门',1,'image','old.png'],
        'scene_error':'保留原失败原因' if status=='failed' else None,
        'representative_blob':'old.png' if representative else None,
        'representative_url':'/api/smarteye/devices/a/d/preview' if representative else None}
    update_device(1,'a','d',state)
    before=get_device(1,'a','d')
    monkeypatch.setattr(device_capture,'get_platform',lambda _:dict(plat_id='a',platform_name='平台',base_url='http://fixture',state='1'))
    monkeypatch.setattr(smart_search,'login_platform',lambda _:dict(success=True))
    monkeypatch.setattr(ledger_image_downloader,'query_ledger_record_page',lambda *args,**kwargs:([dict(deviceId='d',success=True,backPath='/latest.png')],999))
    class Response:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def raise_for_status(self):pass
    class Session(Response):
        def get(self,*args,**kwargs):return Response()
    monkeypatch.setattr(device_capture.requests,'Session',Session)
    monkeypatch.setattr(device_capture,'response_bytes',lambda _:raw.getvalue())
    r.create(task,'device-capture',{'status':'running'},{'tenant_id':1,'user_id':1,'min_long_edge':0})
    result=runtime.tracked_run(lambda id:device_capture._capture_device(id,runtime.actor.get(),
        {'plat_id':'a','device_id':'d'},0,60),task)
    return before,get_device(1,'a','d'),result


@pytest.mark.parametrize('status,source,representative',[
    ('completed','auto',True),('completed','manual',True),('unclassified','auto',False),
    ('failed','auto',True),('processing','auto',True),('completed','auto',False)])
def test_capture_only_ingests_and_inherits_without_changing_device_scene(scenes,monkeypatch,status,source,representative):
    from integrations.smarteye import runtime
    from models import LibraryImage
    before,after,result=capture(scenes,monkeypatch,status=status,source=source,representative=representative)
    assert result['downloaded']==1
    for key in ['scene_source','scene_status','scene_labels','scene_input','scene_revision','scene_error','representative_blob','representative_url']:
        assert after.get(key)==before.get(key),key
    scenes[0].process_pending()
    with runtime.get_runtime().engine.connect() as conn:
        image=conn.execute(select(LibraryImage.__table__)).mappings().one()
        assert image['scene_labels']==(before['scene_labels'] if status=='completed' else [])
        assert image['scene_config'] is None
        assert image['scene_metadata']['device_scene_status']==status
        assert conn.execute(select(runtime.jobs.c.task_id).where(runtime.jobs.c.kind=='device-scenes')).first() is None


def test_user_can_manually_reclassify_after_capture(scenes,monkeypatch):
    from integrations.smarteye import runtime
    from integrations.smarteye.management import get_device
    module,_,_,calls=scenes
    capture(scenes,monkeypatch)
    r=runtime.get_runtime();r.create('manual','device-scenes',{'status':'running'},runtime.actor.get())
    runtime.tracked_run(module.run_classification,'manual',[{'plat_id':'a','device_id':'d'}],None,True)
    assert calls
    assert get_device(1,'a','d')['scene_labels']==['工厂']
    assert get_device(1,'a','d')['scene_status']=='completed'


def test_manual_classification_still_preserves_human_correction_after_capture(scenes,monkeypatch):
    from integrations.smarteye import runtime
    from integrations.smarteye.management import get_device
    module,_,_,calls=scenes
    capture(scenes,monkeypatch,source='manual')
    r=runtime.get_runtime();r.create('manual','device-scenes',{'status':'running'},runtime.actor.get())
    runtime.tracked_run(module.run_classification,'manual',[{'plat_id':'a','device_id':'d'}],None,True)
    assert calls==[]
    assert get_device(1,'a','d')['scene_labels']==['河边']
