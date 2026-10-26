"""Only discover each environment's playbook/inventory; no Ansible task runs."""
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend/backend'))
from services.training_runs import bridge

results=[]
for server in ('嘉兴移动k8s','绍兴移动k8s'):
    value=bridge(server,dict(executor='image_preload',payload=dict(action='preflight',target={})))
    results.append(dict(server=server,playbook=value['playbook'],inventory=value['inventory'],nodes=value['node_count'],
        nodes_hash=value['nodes_hash'],playbook_sha256=value['playbook_sha256'],inventory_sha256=value['inventory_sha256']))
    syntax=bridge(server,dict(executor='image_preload',payload=dict(action='syntax_check',target={},image='aisfh5.com:9091/nexusai/nexusai-http:v20261020194004')))
    results[-1]['syntax_check']=syntax
    assert syntax['ok'],syntax
print(json.dumps(dict(results=results,ansible_tasks_executed=False,remote_writes=0),ensure_ascii=False))
