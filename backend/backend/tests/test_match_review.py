"""Query judgments have their own review, independent of image descriptions."""
from uuid import uuid4
import pytest
from sqlalchemy import select
from tests.test_swift_export_api import swift_project


@pytest.mark.asyncio
async def test_review_existing_match_and_reset_when_judgment_changes(client,auth_headers,swift_project,test_db):
    from models.project_work import MatchQuery
    project,crops=swift_project
    query=await test_db.scalar(select(MatchQuery).where(MatchQuery.project_id==project.id,MatchQuery.query['text'].as_string()=='黑衣服的人'))
    await test_db.commit()
    base=f'/api/projects/{project.id}/matches/{query.id}'
    payload={'asset_id':str(crops[1].id),'approved':True,'base_version':query.version}
    response=client.post(base+'/review',headers=auth_headers,json=payload)
    assert response.status_code==200,response.text
    result=response.json();hit=next(h for h in result['hits'] if h['asset_id']==str(crops[1].id))
    assert hit['review_status']=='approved' and hit['reviewer_id']==1
    assert client.post(base+'/review',headers=auth_headers,json=payload).status_code==409
    payload['base_version']=result['version'];payload['asset_id']=str(crops[3].id)
    assert client.post(base+'/review',headers=auth_headers,json=payload).status_code==400
    response=client.put(base,headers=auth_headers,json={'asset_id':str(crops[1].id),'judgment':'incorrect','base_version':result['version']})
    assert response.status_code==200,response.text
    hit=next(h for h in response.json()['hits'] if h['asset_id']==str(crops[1].id))
    assert hit['review_status']=='pending' and hit.get('reviewer_id') is None
    assert client.post(f'/api/projects/{uuid4()}/matches/{query.id}/review',headers=auth_headers,json=payload).status_code==404
    assert client.post(base+'/review',json=payload).status_code==401


@pytest.mark.asyncio
async def test_rejected_matching_relationship_is_excluded_from_training(client,auth_headers,swift_project,test_db):
    from models.project_work import MatchQuery
    import json,zipfile
    from io import BytesIO
    project,crops=swift_project
    query=await test_db.scalar(select(MatchQuery).where(MatchQuery.project_id==project.id,MatchQuery.query['text'].as_string()=='黑衣服的人'))
    await test_db.commit()
    response=client.post(f'/api/projects/{project.id}/matches/{query.id}/review',headers=auth_headers,
        json={'asset_id':str(crops[1].id),'approved':False,'base_version':query.version})
    assert response.status_code==200,response.text
    response=client.get(f'/api/export/projects/{project.id}/export',headers=auth_headers,
        params={'format':'annotation','dataset_format':'infonce','sample_source':'matches'})
    assert response.status_code==200,response.text
    with zipfile.ZipFile(BytesIO(response.content)) as archive:
        samples=[json.loads(line) for line in archive.read('train.jsonl').decode().splitlines()]
        assert len(samples)==2
        assert str(crops[1].id) not in archive.read('train.jsonl').decode()
