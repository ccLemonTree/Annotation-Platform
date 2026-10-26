"""P2-04: synthetic source leakage, budgets and immutable policy evidence."""
from io import BytesIO
from types import SimpleNamespace
import json
import zipfile
from uuid import UUID

import pytest
from models import Asset, AnnotationEvent, ExportSnapshot
from sqlalchemy import select, func
from tests.test_p2_exports import populate
from tests.test_p0_contracts import foreign_annotation


def sample(asset_id, info=None, labels=('人',), **kwargs):
    return (SimpleNamespace(id=asset_id, content_hash=asset_id, device_info=info,
                            original_name='camera_frame_001.png', **kwargs),
            SimpleNamespace(annotation_data={'boxes': [{'label': label} for label in labels]}))


def service():
    from services import dataset_splits
    return dataset_splits


def test_source_groups_are_transitive_and_parent_links_include_original():
    module = service()
    rows = [sample('original'), sample('crop', {'parent_image_id': 'original', 'group_id': 'clip'}),
            sample('frame', {'group_id': 'clip', 'device_id': 'camera'}),
            sample('other', {'device_id': 'camera'}), sample('unknown')]
    groups, sources, missing = module.source_groups(rows)
    assert sorted(sorted(group) for group in groups) == [['crop', 'frame', 'original', 'other'], ['unknown']]
    assert sources['crop']['parent_image_id'] == 'original'
    assert missing == ['unknown']


def test_sequence_groups_and_no_filename_inference_or_invalid_metadata():
    module = service()
    rows = [sample('a', {'sequence_id': 'clip'}), sample('b', {'sequence_id': 'clip'}),
            sample('c', {'group_id': []}), sample('d')]
    groups, _, missing = module.source_groups(rows)
    assert sorted(sorted(group) for group in groups) == [['a', 'b'], ['c'], ['d']]
    assert missing == ['c', 'd']


def test_parent_reference_to_library_id_and_exact_content_groups():
    module = service()
    rows = [sample('original', image_id='library-parent'), sample('crop', {'parent_image_id': 'library-parent'}),
            sample('copy'), sample('unrelated')]
    rows[2][0].content_hash = rows[1][0].content_hash
    groups, _, missing = module.source_groups(rows)
    assert sorted(sorted(group) for group in groups) == [['copy', 'crop', 'original'], ['unrelated']]
    assert missing == ['unrelated']


def test_phash_and_color_threshold_boundaries_and_transitive_near_groups():
    import imagehash
    module = service()
    rows = [sample(key, {'group_id': key}) for key in ('a', 'b', 'c')]
    fingerprints = {'a': (imagehash.hex_to_hash('0000000000000000'), (10, 10, 10)),
                    'b': (imagehash.hex_to_hash('0000000000000001'), (12, 10, 10)),
                    'c': (imagehash.hex_to_hash('0000000000000003'), (14, 10, 10))}
    narrow = module.SplitPolicy(phash_distance=0, color_distance=2)
    splits = {'a': 'train', 'b': 'val', 'c': 'test'}
    assert module.split_report(rows, ['人'], narrow, splits=splits, fingerprints=fingerprints)['can_freeze']
    broad = module.SplitPolicy(phash_distance=1, color_distance=2)
    report = module.split_report(rows, ['人'], broad, suggest=True, fingerprints=fingerprints)
    assert report['groups'] == [['a', 'b', 'c']]
    assert len(set(report['splits'].values())) == 1
    assert not report['can_freeze']


def test_too_few_groups_and_oversized_groups_never_split_to_fill_budget():
    module = service()
    rows = [sample('a', {'group_id': 'shared'}), sample('b', {'group_id': 'shared'})]
    report = module.split_report(rows, ['人'], module.SplitPolicy(training_budget_per_class=1), suggest=True)
    assert set(report['splits']) == {'a', 'b'}
    assert len(set(report['splits'].values())) == 1
    assert not report['can_freeze']


def test_suggestion_keeps_small_train_group_when_largest_group_exceeds_budget():
    module = service()
    rows = [sample('a', {'group_id': 'a'}), sample('b', {'group_id': 'b'})]
    rows += [sample(f'c{i}', {'group_id': 'large-device'}) for i in range(5)]
    report = module.split_report(rows, ['人'], module.SplitPolicy(training_budget_per_class=2), suggest=True)
    assert report['can_freeze']
    assert report['images_by_label_by_split']['train']['人'] == 1
    assert len({report['splits'][f'c{i}'] for i in range(5)}) == 1


def test_suggestion_preserves_all_images_holdouts_and_class_image_budget():
    module = service()
    rows = [sample(str(i), {'group_id': str(i)}, ('人', '猫')) for i in range(12)]
    policy = module.SplitPolicy(training_budget_per_class=3)
    report = module.split_report(rows, ['人'], policy, suggest=True)
    assert set(report['splits']) == {str(i) for i in range(12)}
    assert set(report['splits'].values()) == {'train', 'val', 'test'}
    assert report['images_by_label_by_split']['train'] == {'人': 3}
    assert report['can_freeze'] is True
    assert report == module.split_report(list(reversed(rows)), ['人'], policy, suggest=True)


def test_preview_conflicts_source_unknown_budget_and_missing_holdouts():
    module = service()
    rows = [sample('a', {'parent_image_id': 'parent'}), sample('b', {'parent_image_id': 'parent'}), sample('c')]
    report = module.split_report(rows, ['人'], module.SplitPolicy(require_source=True, training_budget_per_class=1),
                                 splits={'a': 'train', 'b': 'test', 'c': 'train'})
    assert not report['can_freeze']
    assert report['missing_source_asset_ids'] == ['c']
    assert {error['code'] for error in report['errors']} >= {'source_leakage', 'source_missing', 'missing_split', 'training_budget'}


@pytest.mark.asyncio
async def test_source_patch_preview_freeze_and_old_package_compatibility(client, auth_headers, test_project, test_db):
    ids = populate(client, auth_headers, test_project.id)
    root = f'/api/export/projects/{test_project.id}'
    asset = await test_db.get(Asset, UUID(ids[0][0]))
    asset.device_info = {'vendor': 'keep'}
    await test_db.commit()
    for asset_id, _ in ids:
        response = client.patch(root + f'/assets/{asset_id}/split-source', headers=auth_headers,
                                json={'parent_image_id': 'same-parent'})
        assert response.status_code == 200, response.text
    await test_db.refresh(asset)
    assert asset.device_info == {'vendor': 'keep', 'parent_image_id': 'same-parent'}
    splits = {ids[0][0]: 'train', ids[1][0]: 'test'}
    count = await test_db.scalar(select(func.count(AnnotationEvent.id)))
    preview = client.post(root + '/splits/preview', headers=auth_headers, json={'labels': ['人'], 'splits': splits})
    assert preview.status_code == 200, preview.text
    assert not preview.json()['can_freeze']
    assert 'source_leakage' in {e['code'] for e in preview.json()['errors']}
    assert await test_db.scalar(select(func.count(AnnotationEvent.id))) == count
    response = client.post(root + '/snapshots', headers=auth_headers, json={'splits': splits})
    assert response.status_code == 409, response.text
    assert await test_db.scalar(select(func.count(ExportSnapshot.id))) == 0
    await test_db.rollback()
    for asset_id, _ in ids:
        assert client.patch(root + f'/assets/{asset_id}/split-source', headers=auth_headers,
                            json={'parent_image_id': asset_id}).status_code == 200
    policy = {'require_independent': False, 'training_budget_per_class': 2, 'phash_distance': 2, 'color_distance': 8}
    response = client.post(root + '/snapshots', headers=auth_headers, json={'labels': ['人'], 'splits': splits, 'policy': policy})
    assert response.status_code == 201, response.text
    frozen = response.json()
    evidence = frozen['manifest']['split_evidence']
    assert evidence['policy']['phash_distance'] == 2
    assert evidence['policy']['color_distance'] == 8
    assert evidence['policy']['training_budget_per_class'] == 2
    assert evidence['sources'][ids[0][0]]['parent_image_id'] == ids[0][0]
    url = '/api/export/snapshots/' + frozen['id'] + '/download'
    before = client.get(url, headers=auth_headers).content
    client.patch(root + f'/assets/{ids[0][0]}/split-source', headers=auth_headers, json={'group_id': 'later'})
    assert client.get(url, headers=auth_headers).content == before
    with zipfile.ZipFile(BytesIO(before)) as archive:
        assert json.loads(archive.read('manifest.json'))['split_evidence'] == evidence
    current = client.get(root + '/export', headers=auth_headers, params={'format': 'yolo', 'labels': '人'})
    with zipfile.ZipFile(BytesIO(current.content)) as archive:
        manifest = json.loads(archive.read('manifest.json'))
        assert 'split_evidence' not in manifest
        assert len(manifest['assets']) == 2


def test_split_requests_validate_policy_and_source_scope(client, auth_headers, test_project, foreign_annotation):
    root = f'/api/export/projects/{test_project.id}'
    for policy in ({'phash_distance': 65}, {'color_distance': -1}, {'training_budget_per_class': 0}, {'unknown': True}):
        assert client.post(root + '/splits/preview', headers=auth_headers, json={'policy': policy}).status_code == 422
    assert client.post(root + '/splits/preview', json={}).status_code == 401
    assert client.patch(root + f'/assets/{foreign_annotation.asset_id}/split-source', headers=auth_headers,
                        json={'group_id': 'foreign'}).status_code == 404
    assert client.patch(root + f'/assets/{foreign_annotation.asset_id}/split-source', headers=auth_headers,
                        json={'group_id': ' '}).status_code == 422


def test_project_sources_are_scoped_and_available_without_reading_files(client, auth_headers, test_project):
    ids = populate(client, auth_headers, test_project.id)
    root = f'/api/export/projects/{test_project.id}'
    response = client.get(root + '/split-sources', headers=auth_headers)
    assert response.status_code == 200
    assert response.json() == {asset_id: {} for asset_id, _ in ids}
    assert client.get(root + '/split-sources').status_code == 401


def test_configured_thresholds_change_preview_and_freeze_decision(client, auth_headers, test_project):
    ids = populate(client, auth_headers, test_project.id)
    root = f'/api/export/projects/{test_project.id}'
    splits = {ids[0][0]: 'train', ids[1][0]: 'test'}
    policy = {'require_independent': False, 'color_distance': 255}
    response = client.post(root + '/splits/preview', headers=auth_headers, json={'splits': splits, 'policy': policy})
    assert response.status_code == 200, response.text
    assert 'near_duplicate_leakage' in {item['code'] for item in response.json()['errors']}
    assert client.post(root + '/snapshots', headers=auth_headers, json={'splits': splits, 'policy': policy}).status_code == 409
    policy['color_distance'] = 20
    response = client.post(root + '/snapshots', headers=auth_headers, json={'splits': splits, 'policy': policy})
    assert response.status_code == 201, response.text
