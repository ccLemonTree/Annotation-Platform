"""Opt-in real SDK reads. The API's ordinary environment stays lightweight."""
import json
import os
import subprocess
import zipfile
from io import BytesIO
from pathlib import Path
import pytest
from PIL import Image
from tests.test_p2_exports import populate

@pytest.mark.skipif(not os.environ.get('P2_LOADER_PYTHON'), reason='explicit isolated dataset-loader Python required')
@pytest.mark.parametrize('format, frozen', [('yolo', False), ('yolo', True), ('coco', False), ('coco', True), ('yolo', 'train-only'), ('yolo', 'test-only')])
def test_real_loaders_from_unrelated_working_directory(client, auth_headers, test_project, tmp_path, format, frozen):
    ids = populate(client, auth_headers, test_project.id)
    image = BytesIO(); Image.new('RGB', (100, 50), 'green').save(image, format='PNG')
    asset = client.post('/api/assets/upload', headers=auth_headers, params={'project_id':str(test_project.id)},
        files={'file':('same.png', image.getvalue(),'image/png')}).json()['asset_id']
    ann = client.post('/api/annotations',headers=auth_headers,json={'asset_id':asset,
        'annotation_data':{'boxes':[{'x':2,'y':3,'width':20,'height':10,'label':'人'}]}}).json()
    client.post('/api/annotations/'+ann['id']+'/review',headers=auth_headers,json={'approved':True})
    if frozen:
        splits = {ids[0][0]:'train',ids[1][0]:'val',asset:'test'}
        if frozen in ('train-only','test-only'):
            splits = {key:'train' if frozen == 'train-only' else 'test' for key in splits}
        result = client.post(f'/api/export/projects/{test_project.id}/snapshots',headers=auth_headers,
            json={'format':format,'splits':splits})
        assert result.status_code == 201, result.text
        result = client.get('/api/export/snapshots/'+result.json()['id']+'/download',headers=auth_headers)
    else:
        result = client.get(f'/api/export/projects/{test_project.id}/export',headers=auth_headers,params={'format':format})
    assert result.status_code == 200
    root = tmp_path / 'extracted'
    with zipfile.ZipFile(BytesIO(result.content)) as archive:
        archive.extractall(root)
    report = tmp_path / 'loader-result.json'
    script = Path(__file__).resolve().parents[3] / 'scripts/verify_detection_package.py'
    process = subprocess.run([os.environ['P2_LOADER_PYTHON'], str(script), str(root), str(report)],
        cwd=tmp_path, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=90,
        env={**os.environ,'PYTHONIOENCODING':'utf-8'})
    assert process.returncode == 0, process.stdout + process.stderr
    evidence = json.loads(report.read_text('utf-8'))
    assert sum(s['images'] for s in evidence['splits']) == 3
    assert sum(s['boxes'] for s in evidence['splits']) == 3
    assert evidence['negative_images'] == 1
