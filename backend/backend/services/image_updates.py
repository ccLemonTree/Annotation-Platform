"""Image preparation and explicit activation share deployment history and leases."""
import asyncio
from time import monotonic
from fastapi import HTTPException
from services import training_runs,model_deployments as deployments


async def remote(db,row,action,executor='image_update',**values):
    await deployments.authorized(db,row)
    return await asyncio.to_thread(training_runs.bridge,row.target['control_server'],dict(executor=executor,payload=dict(
        action=action,target=row.target,job_id=str(row.id),**values)))


async def inspect_job(db,row):
    await deployments.validate_connections(row)
    inventory=await remote(db,row,'inventory')
    if not deployments.complete_inventory(inventory) or any(not r.get('image_id') for r in inventory['instances']):raise HTTPException(409,'目标实例尚未全部就绪或无法识别原镜像ID，请先核对')
    if row.target['environment']=='k8s' and not inventory['rollout_complete']:raise HTTPException(409,'原工作负载的滚动更新尚未完成')
    preload={}
    if row.target['environment']=='k8s':preload=await remote(db,row,'preflight',executor='image_preload')
    await deployments.save(db,row,'镜像更新预检查通过，已记录全部原镜像和服务配置',inventory=inventory,check_ok=True,preload_info=preload)
    return inventory


async def prepare(db,row):
    baseline=await inspect_job(db,row)
    deadline=monotonic()+14400
    k8s=row.target['environment']=='k8s'
    await deployments.save(db,row,'正在通过该环境Ansible剧本拉取并分发到全部节点，逐节点核验摘要' if k8s else '正在拉取Docker镜像，完成后等待手动生效')
    retry=True
    while monotonic()<deadline:
        receipt=await remote(db,row,'prepare',image=row.source['image'],retry=retry,
            **(dict(executor='image_preload',preflight={k:v for k,v in row.inspection['preload_info'].items() if k!='nodes'},batch_id=str(row.batch_id or row.id)) if k8s else dict(baseline=baseline)));retry=False
        if receipt.get('prepared'):
            await deployments.save(db,row,'Ansible已分发并核验全部 '+str(receipt['node_count'])+' 个节点，等待手动生效' if k8s else 'Docker镜像已准备，镜像ID已记录，等待手动生效','staged',image_receipt=receipt)
            return
        await asyncio.sleep(5)
    raise HTTPException(504,'镜像拉取等待超时，请核对服务器后重试准备；未执行服务更新')


async def activate(db,row,rollback=False):
    await deployments.validate_connections(row)
    baseline=row.inspection['inventory'];receipt=row.inspection.get('image_receipt')
    if not receipt or not receipt.get('prepared'):raise HTTPException(409,'请先完成镜像准备')
    if row.target['environment']=='k8s' and not receipt.get('pinned_image'):raise HTTPException(409,'请先通过Ansible完成全部节点镜像预分发，不能直接让节点回源拉取')
    if row.target['environment']=='k8s' and not rollback:
        verified=await remote(db,row,'verify',executor='image_preload',image=row.source['image'],digest=receipt['digest'])
        await deployments.save(db,row,'生效前已再次核对全部节点的本地镜像摘要',preload_verified=verified)
    operation='rollback' if rollback else 'activate'
    applied=row.inspection.get('applied',[]) if row.inspection.get('runtime_operation')==operation else []
    await deployments.save(db,row,'正在恢复原镜像' if rollback else '正在切换所选服务的镜像',apply_started=True,runtime_operation=operation,applied=applied)
    if row.target['environment']=='k8s':
        image=baseline['controller']['image'] if rollback else receipt['pinned_image']
        await remote(db,row,'apply',baseline=baseline,image=image,rollback=rollback)
    else:
        for instance in baseline['instances']:
            await remote(db,row,'apply',instance=instance,receipt=receipt,rollback=rollback)
            await deployments.save(db,row,instance['name']+'：镜像操作已提交',applied=[*row.inspection.get('applied',[]),dict(name=instance['name'],ok=True)])
    deadline=monotonic()+max(1200,baseline['desired']*600 if row.target['environment']=='k8s' else 1200)
    while monotonic()<deadline:
        inventory=await remote(db,row,'inventory')
        results=[]
        for instance in inventory['instances']:
            if row.target['environment']=='k8s':
                expected=baseline['controller']['image'] if rollback else receipt['pinned_image']
                match=instance['image']==expected and bool(instance['image_id'])
                if not rollback:match=match and instance['image_id'].endswith(receipt['digest'])
                if rollback:match=match and instance['image_id'] in {i['image_id'] for i in baseline['instances']}
            else:
                old=next((i for i in baseline['instances'] if i['name']==instance['name']),None)
                match=bool(old) and instance['image_id']==(old['image_id'] if rollback else receipt['image_id'])
            results.append(dict(instance,ok=instance['ready'] and match,detail='无容器健康检查，仅核验运行状态和镜像ID' if instance.get('health')=='not_configured' else '容器健康检查 / Pod readiness'))
        await deployments.save(db,row,'已读取当前实例镜像及就绪状态',instances=results)
        correct=deployments.complete_inventory(inventory) and all(i['ok'] for i in results)
        if row.target['environment']=='k8s':correct=correct and inventory['controller']['uid']==baseline['controller']['uid'] and inventory['controller'].get('pull_policy')=='Never' and inventory['rollout_complete'] and inventory.get('token')==str(row.id)+':'+operation
        if correct:
            await deployments.save(db,row,'全部指定实例镜像ID与状态核验通过','rolled_back' if rollback else 'active')
            return
        await asyncio.sleep(10)
    raise HTTPException(504,'镜像更新尚未全部就绪，请核对或恢复旧镜像；不能认定整批成功')


async def refresh(db,row):
    receipt=await remote(db,row,'receipt')
    if receipt.get('prepared') and not receipt.get('mutated') and row.inspection.get('apply_started') and row.status in ('unknown','activation_failed','rollback_failed'):
        await deployments.save(db,row,'已确认尚未提交任何服务变更，返回待生效；外部配置如有变化，请新建记录重新检查','staged',apply_started=False)
        row.error=None
        from services import distribution_center
        await distribution_center.release_lease(db,row)
        return row
    inventory=await remote(db,row,'inventory')
    if row.status in deployments.BUSY:row.status='unknown'
    if row.status=='unknown' and not row.inspection.get('apply_started'):
        row.status='staged' if receipt.get('prepared') else 'transfer_failed'
        row.error=None if receipt.get('prepared') else '准备未确认完成，可重新准备；未自动操作服务'
    await deployments.save(db,row,'只读核对当前镜像，未执行拉取或服务变更',remote_inventory=inventory)
    if row.status in ('staged','transfer_failed'):
        from services import distribution_center
        await distribution_center.release_lease(db,row)
    return row
