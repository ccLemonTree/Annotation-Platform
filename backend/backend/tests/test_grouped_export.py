"""Whole similarity groups stay together in previews and frozen datasets."""
from types import SimpleNamespace
import pytest
from services.detection_export import simple_split
from tests.test_p2_exports import populate


def rows_for(groups):
    return [(SimpleNamespace(id=key),SimpleNamespace(annotation_data={'boxes':[{'label':'person'}]}))
            for group in groups for key in group]


def test_grouped_split_is_deterministic_and_approaches_requested_ratios():
    groups=[list('abcde'),list('fgh'),list('ij')]
    rows=rows_for(groups)
    result=simple_split(rows,['person'],'ratio',{'train':70,'val':30,'test':0},{},groups=groups)
    assert result['images_by_split']=={'train':7,'val':3}
    assert result==simple_split(list(reversed(rows)),['person'],'ratio',{'train':70,'val':30,'test':0},{},groups=list(reversed(groups)))
    assert set(result['splits'])==set('abcdefghij')
    assert all(len({result['splits'][key] for key in group})==1 for group in groups)
    assert result['grouping']['group_count']==3 and result['grouping']['largest_group']==5
    assert result['grouping']['actual_ratios']=={'train':70,'val':30,'test':0}


@pytest.mark.parametrize('mode',['ratio','train_only','val_only'])
def test_grouped_split_respects_whole_group_budgets_and_zero_ratios(mode):
    groups=[list('ab'),list('cd'),['e']]
    result=simple_split(rows_for(groups),['person'],mode,{'train':100,'val':0,'test':0},{'person':1},groups=groups)
    if mode=='val_only':assert result['images_by_split']=={'val':5}
    else:
        assert result['images_by_split']=={'train':1}
        assert set(result['excluded_asset_ids'])==set('abcd')
    assert all(len({result['splits'].get(key,'excluded') for key in group})==1 for group in groups)


def test_three_way_grouped_split_and_multiple_labels_count_images_not_boxes():
    groups=[[str(i)] for i in range(10)]
    rows=rows_for(groups)
    for _,ann in rows:ann.annotation_data['boxes'] += [{'label':'person'},{'label':'bike'}]
    result=simple_split(rows,['person','bike'],'ratio',{'train':30,'val':40,'test':30},{'person':3,'bike':3},groups=groups)
    assert result['images_by_split']=={'train':3,'val':4,'test':3}
    assert result['labels_by_split']['train']=={'person':3,'bike':3}


@pytest.mark.asyncio
async def test_grouping_combines_source_and_similarity_links_transitively(monkeypatch):
    import imagehash
    from services import detection_export
    rows=rows_for([list('abcd')])
    for asset,_ in rows:
        asset.content_hash=asset.id;asset.device_info={'group_id':'clip'} if asset.id in 'ab' else {}
    values={'a':0,'b':15,'c':7,'d':2**64-1}
    async def fingerprints(rows):return {key:(imagehash.hex_to_hash(f'{value:016x}'),(0,0,0)) for key,value in values.items()}
    monkeypatch.setattr(detection_export,'image_fingerprints',fingerprints)
    config=dict(mode='ratio',ratios={'train':50,'val':50,'test':0},budgets={},group_similar=True,
        check_duplicates=True,phash_distance=1,color_distance=0,require_source=False)
    result=await detection_export.prepare_selection(rows,['person'],config)
    assert result['can_freeze'] and result['grouping']['group_count']==2
    assert len({result['splits'][key] for key in 'abc'})==1
    assert result['leakage_report']['errors']==[]


def test_grouped_preview_and_freeze_use_same_whole_groups(client,auth_headers,test_project):
    populate(client,auth_headers,test_project.id)
    root=f'/api/export/projects/{test_project.id}'
    config=dict(mode='ratio',ratios={'train':50,'val':50,'test':0},budgets={},
        group_similar=True,check_duplicates=False,phash_distance=0,color_distance=255,require_source=False)
    body=dict(labels=['人'],selection=config)
    preview=client.post(root+'/splits/preview',headers=auth_headers,json=body)
    assert preview.status_code==200 and preview.json()['can_freeze'],preview.text
    planned=preview.json()
    assert planned['grouping']['group_count']==1 and planned['grouping']['largest_group']==2
    assert planned['grouping']['empty_requested_splits']==['val']
    assert len(set(planned['splits'].values()))==1
    frozen=client.post(root+'/snapshots',headers=auth_headers,json=body)
    assert frozen.status_code==201,frozen.text
    manifest=frozen.json()['manifest']
    assert {item['asset_id']:item['split'] for item in manifest['assets']}==planned['splits']
    assert manifest['selection']['group_similar'] and manifest['selection']['near_duplicate_checked']
    assert manifest['selection']['grouping']==planned['grouping']
    assert manifest['split_evidence']['can_freeze']


def test_grouping_does_not_bypass_required_source_metadata(client,auth_headers,test_project):
    populate(client,auth_headers,test_project.id)
    body=dict(selection=dict(group_similar=True,require_source=True))
    root=f'/api/export/projects/{test_project.id}'
    preview=client.post(root+'/splits/preview',headers=auth_headers,json=body)
    assert preview.status_code==200 and not preview.json()['can_freeze']
    assert any(item['code']=='source_missing' for item in preview.json()['errors'])
    assert client.post(root+'/snapshots',headers=auth_headers,json=body).status_code==409
