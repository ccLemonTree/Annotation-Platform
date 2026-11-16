"""Inline device prompts apply to images and the name-only fallback."""
from PIL import Image
import pytest
from integrations.smarteye import runtime
from integrations.smarteye.management import update_device,get_device
from schemas.image_ingest import ImageIngestConfig
from tests.test_device_scenes import scenes


@pytest.mark.parametrize('with_image',[False,True])
def test_device_classification_consumes_saved_scene_prompt(scenes,monkeypatch,with_image):
    module,_,run,calls=scenes
    prompt='本页自定义场景规则：作业生产区域归入工厂，保留多场景。'
    config=ImageIngestConfig(classification_enabled=True,scene_prompt=prompt)
    monkeypatch.setattr(module,'config_for',lambda tenant_id:config)
    values={'device_name':'工厂大门'}
    if with_image:
        Image.new('RGB',(12,12),'red').save(runtime.get_runtime().root/'capture.png')
        values['representative_blob']='capture.png'
    update_device(1,'a','inline',values)
    task=run()
    assert task['processed_count']==1 and task['failed_count']==0
    text=calls[0]['json']['messages'][0]['content'][0]['text']
    assert prompt in text
    assert '候选' in text and 'labels' in text
    if not with_image:
        assert '设备名称' in text and '不使用图片' in text
        assert len(calls[0]['json']['messages'][0]['content'])==1
    assert get_device(1,'a','inline')['scene_labels']==['工厂']


@pytest.mark.asyncio
async def test_name_prompt_still_rejects_labels_outside_candidates():
    import json
    import httpx
    from integrations.smarteye.device_scenes import infer_device_labels
    class Client:
        async def post(self,url,**kwargs):
            return httpx.Response(200,request=httpx.Request('POST',url),json={'choices':[{
                'message':{'content':json.dumps({'labels':['不在候选列表的自造场景']})}}]})
    with pytest.raises(ValueError,match='名单之外'):
        await infer_device_labels('工厂大门',ImageIngestConfig(classification_enabled=True,scene_prompt='自定义要求'),Client())
