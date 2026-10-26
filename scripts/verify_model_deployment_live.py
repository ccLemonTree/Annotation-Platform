"""Read-only deployment discovery: no upload, packaging, load, unload or restart."""
import json
from pathlib import Path
import sys
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend/backend'))
from services.training_runs import bridge


def run():
    available={s['name']:s for s in bridge('',dict(list_servers=True))}
    profiles=[
        dict(name='shaoxing-small-k8s',file_server='绍兴nfs',control_server='绍兴移动k8s',environment='k8s',runtime='triton_plan',repository='/data/ai/chen/code/huggingface/aiseefor_small_model',model_name='rfdetr_2xlargemd',workload='nexusai-triton-prod'),
        dict(name='shaoxing-large-k8s',file_server='绍兴nfs',control_server='绍兴移动k8s',environment='k8s',runtime='triton_vllm',repository='/data/ai/chen/code/huggingface/aiseefor_vllm_model',model_name='cangqiong_4b',workload='vllm-triton-prod'),
        dict(name='hangzhou-docker',file_server='杭州移动小模型',control_server='杭州移动小模型',environment='docker',runtime='triton_plan',repository='/ai/chen/code/huggingface/aiseefor_model',model_name='yolov11det_smoking',containers=['triton-server']),
        dict(name='lishui-kafka',file_server='丽水gpu2',control_server='丽水gpu2',environment='docker_kafka',runtime='triton_plan',repository='/ai/chen/code/huggingface/aiseefor_model',model_name='yolov11det_smoking',containers=['triton-server1','triton-server2','triton-server3','triton-server4']),
        dict(name='shaoxing-vector',file_server='绍兴移动算法1',control_server='绍兴移动算法1',environment='docker',runtime='vllm',repository='/opt/gme-model-cache',model_name='0923_merged-4083',containers=['wemm-vllm']),
    ]
    reports=[]
    for values in profiles:
        target={**dict(namespace='ai',workload_kind='StatefulSet',container='triton-server',containers=[]),**values}
        target['connections']={name:available[name] for name in (target['file_server'],target['control_server'])}
        def remote(server,action,**kwargs):
            return bridge(server,dict(executor='model_deployment',payload=dict(action=action,target=target,job_id=str(uuid4()),**kwargs)))
        try:
            state=remote(target['file_server'],'state')
            target['file_addresses']=state['addresses']+[available[target['file_server']]['host']]
            target['nfs4_roots']=state.get('nfs4_roots',[])
            inventory=remote(target['control_server'],'inventory')
            ready=sum(bool(i['ready']) for i in inventory['instances'])
            assert len(inventory['instances'])==inventory['desired'],'incomplete instance inventory'
            report=dict(profile=values['name'],instances=len(inventory['instances']),ready=ready,desired=inventory['desired'],
                path=state['path'],internal_model_path=inventory['internal_model_path'],read_only=True)
            if target['runtime']=='triton_plan':report['sample_compatibility']=remote(target['control_server'],'compatibility',instance=inventory['instances'][0])
            if target['runtime']=='vllm':report['model_list']=remote(target['control_server'],'probe',instance=inventory['instances'][0])
            reports.append(report)
        except Exception as error:reports.append(dict(profile=values['name'],error=str(error),read_only=True))
        print(json.dumps(reports[-1],ensure_ascii=False),flush=True)
    out=ROOT/'output/model-deployment-live.json';out.write_text(json.dumps(dict(ssh_server_count=len(available),profiles=reports,remote_writes=0),ensure_ascii=False,indent=2),encoding='utf-8')
    if any('error' in r for r in reports):raise SystemExit(1)


if __name__=='__main__':run()
