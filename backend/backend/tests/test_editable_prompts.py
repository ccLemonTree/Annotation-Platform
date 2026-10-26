"""Editable Qwen instructions: persistence and the actual outbound request, isolated only."""
import json
from io import BytesIO

import httpx
import pytest
import pytest_asyncio
from PIL import Image
from pydantic import ValidationError


@pytest_asyncio.fixture(autouse=True)
async def model_table(test_engine):
    from models.model_service import ModelService
    async with test_engine.begin() as connection:
        await connection.run_sync(lambda conn: ModelService.__table__.create(conn, checkfirst=True))


def config(**changes):
    return dict(key='qwen', name='Qwen', kind='caption', protocol='openai_chat',
                base_url='http://qwen.test/v1', endpoint='/chat/completions', model_name='Qwen3.8-27B', **changes)


def image_bytes():
    image = BytesIO()
    Image.new('RGB', (32, 24), 'white').save(image, 'PNG')
    return image.getvalue()


def test_prompt_defaults_and_model_persistence(client, auth_headers):
    defaults = client.get('/api/model-services/prompt-defaults', headers=auth_headers)
    assert defaults.status_code == 200, defaults.text
    defaults = defaults.json()
    assert all(defaults[key].strip() for key in ('caption', 'review', 'match_review', 'scene'))
    assert client.get('/api/model-services/prompt-defaults').status_code == 401
    created = client.post('/api/model-services', headers=auth_headers, json=config())
    assert created.status_code == 201, created.text
    record = created.json()
    assert record['chat_prompts'] == {key: defaults[key] for key in ('caption', 'review','match_review','training_analysis')}
    prompts = dict(caption='只描述设备和人员。', review='重点核对遮挡目标，无法确认时返回uncertain。',match_review='只判断查询要求，模糊时uncertain。',training_analysis='仅按提供的训练指标分析。')
    url = '/api/model-services/' + record['id']
    saved = client.put(url, headers=auth_headers, json=config(chat_prompts=prompts))
    assert saved.status_code == 200, saved.text
    assert saved.json()['revision'] == 2 and saved.json()['chat_prompts'] == prompts
    assert client.get('/api/model-services', headers=auth_headers).json()[0]['chat_prompts'] == prompts
    for invalid in ({'caption': '   '}, {'review': 'x' * 8001}, {'match_review':' '}, {'other': 'unknown'}):
        assert client.put(url, headers=auth_headers, json=config(chat_prompts=invalid)).status_code == 422
    restored = client.put(url, headers=auth_headers, json=config(chat_prompts={}))
    assert restored.json()['chat_prompts']['review'] == defaults['review']


@pytest.mark.asyncio
async def test_prompt_permissions_and_tenant_scope(client, auth_headers, test_db, test_user):
    created = client.post('/api/model-services', headers=auth_headers, json=config()).json()
    test_user.role = 'annotator'
    await test_db.commit()
    assert client.get('/api/model-services/prompt-defaults', headers=auth_headers).status_code == 200
    assert client.put('/api/model-services/' + created['id'], headers=auth_headers,
                      json=config(chat_prompts={'caption': '更改描述'})).status_code == 403
    test_user.role = 'admin'; test_user.tenant_id = 2
    from models import Tenant
    test_db.add(Tenant(id=2, tenant_name='other')); await test_db.commit()
    assert client.put('/api/model-services/' + created['id'], headers=auth_headers, json=config()).status_code == 404


@pytest.mark.asyncio
async def test_saved_caption_reaches_single_and_batch_requests_and_override_wins():
    from schemas.model_service import ModelServiceConfig
    from services.model_inference import infer
    from services.image_semantics import caption
    model = ModelServiceConfig(**config(chat_prompts={'caption': '仅说明设备位置。'}))
    seen = []
    def handle(request):
        body = json.loads(request.content)
        seen.append(body['messages'][0]['content'][0]['text'])
        return httpx.Response(200, json={'choices': [{'message': {'content': '设备位于左侧'}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        await infer(model, image_bytes(), 32, 24, client=client)
        await caption(model, image_bytes(), '', client)
        await infer(model, image_bytes(), 32, 24, '本次仅描述人员。', client)
        await caption(model, image_bytes(), '本批仅描述车辆。', client)
    assert seen == ['仅说明设备位置。', '仅说明设备位置。', '本次仅描述人员。', '本批仅描述车辆。']


@pytest.mark.asyncio
async def test_saved_review_reaches_request_with_runtime_context_and_validation():
    from schemas.model_service import ModelServiceConfig
    from services.automation_review import audit_image, auto_pass
    from fastapi import HTTPException
    model = ModelServiceConfig(**config(chat_prompts={'review': '重点核对人员是否遮挡。'}))
    boxes = [dict(x=1, y=2, width=10, height=12, label='person', score=.95)]
    seen = []
    def handle(request):
        text = json.loads(request.content)['messages'][0]['content'][0]['text']
        seen.append(text)
        assert '重点核对人员是否遮挡。' in text and '本次检查安全帽' in text
        assert '本任务整图完整性及负样本检查仅限以下类别：' in text and 'person' in text and '本轮编号：[0]' in text
        assert '"completeness"' in text and '"boxes"' in text
        return httpx.Response(200, json={'choices': [{'message': {'content': '{}'}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(HTTPException) as error:
            await audit_image(model, image_bytes(), boxes, ['person'], .2, '本次检查安全帽', client)
    assert error.value.status_code == 502 and len(seen) == 1
    assert not auto_pass([], dict(boxes=[], completeness='pass', negative='pass', reason='无目标'), .9, True)


def test_scene_prompt_persists_per_collection_and_validates(client, auth_headers):
    first = client.post('/api/images/collections', headers=auth_headers, json={'name': 'first'}).json()['id']
    second = client.post('/api/images/collections', headers=auth_headers, json={'name': 'second'}).json()['id']
    url = '/api/images/ingest-settings'
    original = client.get(url, headers=auth_headers, params={'collection_id': second}).json()
    assert original['scene_prompt']
    saved = client.put(url, headers=auth_headers, params={'collection_id': first},
                       json={**original, 'scene_prompt': '重点判断摄像头视角。'})
    assert saved.status_code == 200, saved.text
    assert client.get(url, headers=auth_headers, params={'collection_id': first}).json()['scene_prompt'] == '重点判断摄像头视角。'
    assert client.get(url, headers=auth_headers, params={'collection_id': second}).json()['scene_prompt'] == original['scene_prompt']
    from schemas.image_ingest import ImageIngestConfig
    for value in ('  ', 'x' * 8001):
        with pytest.raises(ValidationError): ImageIngestConfig(scene_prompt=value)


def test_legacy_configs_and_empty_semantic_override_use_defaults():
    from schemas.model_service import ModelServiceConfig
    from schemas.image_ingest import ImageIngestConfig
    from schemas.image_semantic import SemanticJobCreate
    model = ModelServiceConfig(**config())
    assert model.chat_prompts.caption and model.chat_prompts.review
    assert ImageIngestConfig().scene_prompt
    job = SemanticJobCreate(image_ids=['11111111-1111-4111-8111-111111111111'],
                            caption_model_id='22222222-2222-4222-8222-222222222222', prompt='')
    assert job.prompt == ''
