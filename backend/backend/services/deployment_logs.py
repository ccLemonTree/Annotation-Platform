"""Administrator log snapshots; never schedule or mutate deployments."""
import asyncio
from datetime import datetime,timezone
import hashlib
import json
from fastapi import HTTPException
from services import model_deployments,training_runs
from services.deployment_logs_executor import bounded

_slots=asyncio.Semaphore(2)


async def snapshot(db,row,source,instance=None):
    server=row.target['control_server']
    history='\n'.join(str(log.get('at',''))+'  '+str(log.get('message','')) for log in row.logs[-200:]) if source=='updates' else ''
    result=dict(content='',truncated=False,instances=[],selected_instance=None)
    if source=='service' or row.kind=='image':
        await model_deployments.validate_connections(row)
        payload=dict(source=source,target=row.target,job_id=str(row.id),instance=instance)
        if source=='updates' and row.target['environment']=='k8s':
            info=row.inspection.get('preload_info')
            if info:
                payload['cache_key']=hashlib.sha256(json.dumps([str(row.batch_id or row.id),row.source['image'],
                    {k:v for k,v in info.items() if k!='nodes'}],sort_keys=True).encode()).hexdigest()
        try:await asyncio.wait_for(_slots.acquire(),0.1)
        except TimeoutError:raise HTTPException(429,'日志读取繁忙，请稍后重试') from None
        try:
            result=await asyncio.to_thread(training_runs.bridge,server,dict(executor='deployment_logs',payload=payload))
        finally:_slots.release()
    text=bounded(history+('\n' if history and result.get('content') else '')+result.get('content',''))
    return dict(text,source=source,server=server,status=row.status,
        truncated=text['truncated'] or result.get('truncated',False) or source=='updates' and len(row.logs)>200,
        instances=[dict(name=r['name'],uid=r['uid']) for r in result.get('instances',[])],
        selected_instance=result.get('selected_instance'),collected_at=datetime.now(timezone.utc).isoformat())
