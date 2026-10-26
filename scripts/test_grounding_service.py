import importlib.util
import sys
from types import SimpleNamespace
from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image


def service():
    path = Path(__file__).with_name('grounding_service.py')
    spec = importlib.util.spec_from_file_location('grounding_service', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_groundingpi_scales_coordinates_and_preserves_absent_targets():
    module = service()
    rows = module.groundingpi_boxes([
        {'label': 'car', 'coordinates': ((0, 100, 999, 899),)},
        {'label': 'unicorn', 'coordinates': None},
    ], 1920, 1080)
    assert rows[0]['bbox'] == pytest.approx([0, 1080*100/999, 1920, 1080*899/999])
    assert rows[0]['score'] == .5 and len(rows) == 1


def test_http_validates_images_and_passes_requested_categories():
    module = service()
    calls = []
    class Engine:
        def detect(self, image, categories):
            calls.append((image.size, categories))
            return {'detections': [{'class': categories[0], 'bbox': [0,0,10,10], 'score': .5}], 'raw_output': 'ok'}
    content = BytesIO(); Image.new('RGB', (20, 20)).save(content, 'PNG')
    with TestClient(module.create_app(Engine())) as client:
        result = client.post('/detect/grounding', files={'image': ('test.png', content.getvalue())},
                             data={'prompt': 'electric bicycle; person', 'conf':'.99', 'nms':'.01'})
        assert result.status_code == 200
        assert result.json()['score_type'] == 'uncalibrated'
        assert len(result.json()['detections']) == 1
        assert calls == [((20,20), ['electric bicycle', 'person'])]
        assert client.get('/health').json()['status'] == 'ok'
        assert client.post('/detect/grounding', files={'image': ('bad', b'bad')}).status_code == 400
        assert client.post('/detect/grounding', files={'image': ('a', content.getvalue())},
                           data={'labels': '[42]'}).status_code == 400


def test_category_validation():
    module = service()
    assert module.categories_from_request('{"car":{"conf":0.25}}', '') == ['car']
    assert module.categories_from_request('{}', '') == ['electric bicycle']
    for labels in ('not json', '{"":{}}', '[null]'):
        with pytest.raises(ValueError): module.categories_from_request(labels, '')


def test_vlx_rejects_truncated_generation(monkeypatch):
    module = service()
    monkeypatch.setitem(sys.modules, 'inference', SimpleNamespace(load_wedetect_proposals=lambda *a: []))
    engine = module.VLXEngine.__new__(module.VLXEngine)
    engine.worker = SimpleNamespace(detect=lambda *a, **k: {'completion_tokens':1024})
    with pytest.raises(ValueError, match='partial boxes'):
        engine.detect(Image.new('RGB', (20,20)), ['car'])


def test_http_restores_original_resolution_and_hides_engine_failures():
    module = service()
    class Engine:
        def detect(self, image, categories):
            if categories == ['fail']:
                raise RuntimeError('private runtime traceback')
            return {'detections':[{'class':'car','bbox':[0,0,*image.size],'score':.5}]}
    content = BytesIO(); Image.new('RGB', (2000,1000)).save(content, 'PNG')
    with TestClient(module.create_app(Engine())) as client:
        response = client.post('/detect/grounding', files={'image':('a', content.getvalue())})
        assert response.json()['detections'][0]['bbox'] == [0,0,2000,1000]
        response = client.post('/detect/grounding', files={'image':('a', content.getvalue())}, data={'prompt':'fail'})
        assert response.status_code == 502 and 'private' not in response.text
