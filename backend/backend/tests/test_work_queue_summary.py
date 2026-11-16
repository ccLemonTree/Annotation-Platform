from uuid import uuid4
import pytest
from models import Asset
from models.project_work import ProjectWorkJob,ProjectWorkItem
from tests.test_project_work import work_env


@pytest.mark.asyncio
async def test_pipeline_roots_group_children_before_pagination(client,auth_headers,work_env):
    from services import project_work as service
    s=work_env;s.project.task_type='caption'
    config={'target_label':'car','fields':[{'key':'color','name':'颜色','instruction':'','options':[]}]}
    root=ProjectWorkJob(project_id=s.project.id,user_id=s.user.id,operation='detect_attributes',model={'id':str(s.models[0].id),'revision':1,'config':{'name':'SAM3'}},options={'attribute_schema':config,'attribute_model':{'config':{'name':'Qwen'}}})
    s.db.add(root);await s.db.flush()
    child_ids=[]
    for index in range(26):
        source=Asset(project_id=s.project.id,original_name=f'original-{index}.jpg',blob_path='fixture',content_hash=str(index),file_size=1,source_type='upload')
        s.db.add(source);await s.db.flush()
        result=None
        if index<25:
            child=ProjectWorkJob(project_id=s.project.id,user_id=s.user.id,operation='attributes',model={'id':str(s.models[1].id),'revision':1,'config':{'name':'Qwen'}},options={'attribute_schema':config})
            s.db.add(child);await s.db.flush();child_ids.append(child.id)
            for target in range(3):
                s.db.add(ProjectWorkItem(job_id=child.id,asset_id=None,content_hash='fixture',status='completed' if index==0 else 'failed' if index==1 else 'pending',error='模型服务超时' if index==1 else None))
            result={'attribute_job_id':str(child.id),'detected_count':3,'skipped_annotated_count':0}
        s.db.add(ProjectWorkItem(job_id=root.id,asset_id=source.id,content_hash=source.content_hash,status='completed' if index<25 else 'pending',result=result))
    await s.db.commit()
    response=client.get(f'/api/projects/{s.project.id}/work/jobs',headers=auth_headers,params={'roots_only':True})
    assert response.status_code==200,response.text
    rows=response.json();assert [row['id'] for row in rows]==[str(root.id)]
    pipeline=rows[0]['pipeline'];assert pipeline['detection']['total']==26 and pipeline['detection']['counts']=={'completed':25,'pending':1}
    assert pipeline['attributes']['total']==75 and pipeline['attributes']['counts']=={'completed':3,'failed':3,'pending':69}
    assert pipeline['generated_targets']==75 and pipeline['child_batches']==25
    assert rows[0]['model_name']=='SAM3' and rows[0]['attribute_model_name']=='Qwen'
    assert rows[0]['target_labels']==['car'] and '检测' in rows[0]['waiting_reason']
    assert rows[0]['failure_reasons']==[{'operation':'attributes','reason':'模型服务超时','count':3}]
    assert client.get(f'/api/projects/{s.project.id}/work/jobs',headers=auth_headers,params={'roots_only':True,'page':2}).json()==[]
    detail=await service.summary(s.db,root,True)
    assert any(item['original_name'].startswith('original-') for item in detail['items'])
    child=await s.db.get(ProjectWorkJob,child_ids[0]);child_detail=await service.summary(s.db,child,True)
    assert child_detail['parent']['job_id']==str(root.id) and child_detail['parent']['original_name']=='original-0.jpg'
