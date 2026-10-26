"""Read-only image inventory through the configured ssh-manager MCP bridge."""
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend/backend'))
from services.training_runs import bridge

profiles=[
    ('杭州移动小模型',dict(environment='docker',containers=['nexusai','triton-server'])),
    ('丽水gpu2',dict(environment='docker_kafka',containers=['nexusai-worker1','nexusai-worker2'])),
    ('绍兴移动算法1',dict(environment='docker',containers=['wemm-vllm'])),
    ('绍兴移动k8s',dict(environment='k8s',namespace='ai',workload_kind='StatefulSet',workload='nexusai-triton-prod',container='nexusai')),
]
result=[]
for server,target in profiles:
    try:
        value=bridge(server,dict(executor='image_update',payload=dict(action='inventory',job_id='00000000-0000-0000-0000-000000000001',target=target)))
        result.append(dict(server=server,desired=value['desired'],ready=sum(i['ready'] for i in value['instances']),
            instances=len(value['instances']),images=sorted({i['image'] for i in value['instances']}),rollout_complete=value['rollout_complete'],
            compose=sum(bool(i.get('compose')) for i in value['instances'])))
    except Exception as error:result.append(dict(server=server,error=str(getattr(error,'detail',error))[:1200]))
print(json.dumps(dict(remote_writes=0,results=result),ensure_ascii=False))
assert all('error' not in row and row['desired']==row['ready']==row['instances'] for row in result)
