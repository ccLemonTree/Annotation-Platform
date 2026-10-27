"""Freeze conflicts are visible before any ZIP is created."""
import pytest
from sqlalchemy import select,func
from models import ExportSnapshot
from tests.test_p2_exports import populate


def selection(check_duplicates):
    return dict(mode='ratio',ratios=dict(train=50,val=50,test=0),budgets={},
                check_duplicates=check_duplicates,phash_distance=0,color_distance=255,require_source=False)


@pytest.mark.asyncio
async def test_simple_preview_exposes_near_duplicate_conflicts_before_packaging(client,auth_headers,test_project,test_db,monkeypatch):
    from services import detection_export
    populate(client,auth_headers,test_project.id)
    root=f'/api/export/projects/{test_project.id}'
    body=dict(labels=['人'],selection=selection(True))
    preview=client.post(root+'/splits/preview',headers=auth_headers,json=body)
    assert preview.status_code==200,preview.text
    assert preview.json()['can_freeze'] is False
    assert preview.json()['errors'][0]['code']=='near_duplicate_leakage'
    def no_archive(*args,**kwargs):raise AssertionError('conflict must be rejected before opening a ZIP')
    monkeypatch.setattr(detection_export.tempfile,'SpooledTemporaryFile',no_archive)
    frozen=client.post(root+'/snapshots',headers=auth_headers,json=body)
    assert frozen.status_code==409,frozen.text
    assert '近重复图片跨数据划分' in frozen.json()['detail']
    assert 'same.png' in frozen.json()['detail'] and '图片' in frozen.json()['detail']
    assert await test_db.scalar(select(func.count()).select_from(ExportSnapshot))==0


def test_disabled_duplicate_check_keeps_ratio_export_compatible(client,auth_headers,test_project):
    populate(client,auth_headers,test_project.id)
    root=f'/api/export/projects/{test_project.id}'
    body=dict(labels=['人'],selection=selection(False))
    preview=client.post(root+'/splits/preview',headers=auth_headers,json=body)
    assert preview.status_code==200 and preview.json()['can_freeze'] is True
    frozen=client.post(root+'/snapshots',headers=auth_headers,json=body)
    assert frozen.status_code==201,frozen.text
    assert frozen.json()['manifest']['selection']['near_duplicate_checked'] is False


def test_duplicate_check_only_validates_images_kept_by_training_budget(client,auth_headers,test_project):
    ids=populate(client,auth_headers,test_project.id)
    annotation=ids[1][1]
    changed=client.put('/api/annotations/'+annotation['id'],headers=auth_headers,json={'base_version':1,
        'annotation_data':{'boxes':[{'label':'人','x':1,'y':1,'width':10,'height':10}]}})
    assert changed.status_code==200,changed.text
    assert client.post('/api/annotations/'+changed.json()['id']+'/review',headers=auth_headers,json={'approved':True}).status_code==200
    root=f'/api/export/projects/{test_project.id}'
    config={**selection(True),'mode':'train_only','budgets':{'人':1}}
    body=dict(labels=['人'],selection=config)
    preview=client.post(root+'/splits/preview',headers=auth_headers,json=body)
    assert preview.status_code==200 and preview.json()['can_freeze'] is True,preview.text
    frozen=client.post(root+'/snapshots',headers=auth_headers,json=body)
    assert frozen.status_code==201,frozen.text
    assert len(frozen.json()['manifest']['assets'])==1
