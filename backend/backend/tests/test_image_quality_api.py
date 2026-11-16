from uuid import uuid4
import pytest
from sqlalchemy import select,func
from models import User,ProjectMember
from models.project_work import ProjectWorkJob
from services.auth import create_access_token
from tests.test_project_work import work_env


def request(s,**changes):
    return dict(operation='quality',model_id=str(s.models[1].id),asset_ids=[str(s.asset.id)],prompt='仅保留完整且主体明确的车辆图片',**changes)


@pytest.mark.asyncio
async def test_quality_api_records_requirements_and_queues_without_deleting(client,auth_headers,work_env):
    s=work_env;path=f'/api/projects/{s.project.id}/work/jobs'
    response=client.post(path,headers=auth_headers,json=request(s))
    assert response.status_code==201,response.text
    data=response.json();assert data['operation']=='quality' and data['counts']=={'pending':1}
    assert data['quality_requirement']==request(s)['prompt']
    assert data['quality_counts']=={'passed':0,'deleted':0}
    assert client.get(f'/api/assets/{s.asset.id}',headers=auth_headers).status_code==200
    detail=client.get(path+'/'+data['id'],headers=auth_headers)
    assert detail.status_code==200 and detail.json()['items'][0]['asset_id']==str(s.asset.id)
    assert client.get(f'/api/projects/{uuid4()}/work/jobs/'+data['id'],headers=auth_headers).status_code==404


@pytest.mark.asyncio
async def test_member_cannot_start_destructive_quality_jobs(client,auth_headers,work_env):
    s=work_env
    member=User(id=2,tenant_id=1,username='quality-member',email='quality-member@test.invalid',password_hash='unused',role='user',is_active=True)
    s.db.add(member);await s.db.flush();s.db.add(ProjectMember(project_id=s.project.id,user_id=member.id));await s.db.commit()
    headers={'Authorization':'Bearer '+create_access_token({'user_id':2})}
    response=client.post(f'/api/projects/{s.project.id}/work/jobs',headers=headers,json=request(s))
    assert response.status_code==403,response.text
    assert await s.db.scalar(select(func.count()).select_from(ProjectWorkJob))==0
    assert client.get(f'/api/assets/{s.asset.id}',headers=auth_headers).status_code==200


@pytest.mark.asyncio
@pytest.mark.parametrize('change',[{'prompt':''},{'prompt':' '*3},{'prompt':'x'*2001},{'asset_ids':[str(uuid4())]}])
async def test_quality_invalid_scope_or_requirements_never_queue(client,auth_headers,work_env,change):
    s=work_env;body=request(s);body.update(change)
    response=client.post(f'/api/projects/{s.project.id}/work/jobs',headers=auth_headers,json=body)
    assert response.status_code in (404,422),response.text
    assert await s.db.scalar(select(func.count()).select_from(ProjectWorkJob))==0
