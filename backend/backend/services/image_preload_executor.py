"""Run the environment's Ansible image preload pipeline; never update workloads."""
import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from uuid import UUID,uuid4
from urllib.parse import urlsplit


def command(args,timeout=90):
    result=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True,timeout=timeout)
    if result.returncode:raise ValueError('Ansible预分发检查命令失败：'+args[0]+'；请在控制服务器核对配置')
    return result.stdout.strip()


def sha(path):
    value=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024**2),b''):value.update(chunk)
    return value.hexdigest()


def fingerprint(value):return hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()


def private(path):
    path=Path(path)
    if any(p.is_symlink() for p in [path]+list(path.parents)):raise ValueError('预分发目录不能经过符号链接')
    path.mkdir(parents=True,exist_ok=True,mode=0o700);os.chmod(str(path),0o700);return path


def root():return Path.home()/'.local/share/data-platform/image-updates'


def job_folder(job):UUID(job);return private(root()/job)


def write(path,value):
    temp=path.with_suffix('.tmp');temp.write_text(json.dumps(value),encoding='utf-8');os.chmod(str(temp),0o600);os.replace(str(temp),str(path))


def pinned(image,digest):
    name=image.split('@')[0];parts=name.rsplit('/',1);parts[-1]=parts[-1].split(':')[0]
    return '/'.join(parts)+'@'+digest


def cluster_nodes():
    rows=json.loads(command(['kubectl','get','nodes','-o','json']))['items']
    result=[dict(name=r['metadata']['name'],uid=r['metadata']['uid'],addresses=sorted(a['address'] for a in r['status']['addresses'] if a['type']=='InternalIP')) for r in rows]
    if not result or len(result)>1000 or any(not r['addresses'] for r in result):raise ValueError('无法识别全部K8s节点地址，或节点数量超过1000')
    return sorted(result,key=lambda r:r['name'])


def cover_inventory(raw,nodes):
    hosts=raw.get('_meta',{}).get('hostvars',{});result={}
    for node in nodes:
        candidates=[(name,value) for name,value in hosts.items() if str(value.get('ansible_host',name)) in node['addresses'] or name==node['name'] and value.get('ansible_host',name)==node['name']]
        if not candidates:raise ValueError('Ansible inventory未覆盖集群节点：'+node['name'])
        candidates.sort(key=lambda v:(v[0]!=node['name'],v[0]));value=copy.deepcopy(candidates[0][1])
        if value.get('ansible_connection')=='local' and not set(node['addresses'])&set(command(['hostname','-I']).split()):raise ValueError('远端节点不能使用local连接：'+node['name'])
        value.setdefault('ansible_host',candidates[0][0]);value['platform_node_uid']=node['uid'];result[node['name']]=value
    return result


def load_plays(path):
    import yaml
    try:plays=yaml.safe_load(Path(path).read_text())
    except Exception:raise ValueError('preload-ai-images.yml格式无法读取') from None
    names=['Prepare Triton archive on controller','Inspect Triton image state on GPU nodes','Rsync Triton archive and start import','Wait for Triton import to finish']
    selected=[play for name in names for play in plays if play.get('name')==name]
    if len(selected)!=4:raise ValueError('预分发剧本缺少明确的Triton拉取/导出/分发/等待段，请核对该环境的preload-ai-images.yml')
    return selected


def preflight(target):
    for binary in ('ansible-playbook','ansible-inventory','ctr','kubectl'):
        if not shutil.which(binary):raise ValueError('控制服务器缺少命令：'+binary)
    requested=target.get('preload_playbook','')
    paths=[Path(requested)] if requested else sorted(Path('/opt').glob('*/ansible/preload-ai-images.yml'))
    paths=[p for p in paths if p.is_file()]
    if len(paths)!=1:raise ValueError('请指定当前K8s环境的preload-ai-images.yml绝对路径；未找到唯一剧本')
    playbook=paths[0];load_plays(playbook);nodes=cluster_nodes();requested=target.get('preload_inventory','')
    if requested:candidates=[Path(requested)]
    else:
        candidates=list(playbook.parent.glob('*.ini'))+list(playbook.parent.glob('*preload*.json'))+list(playbook.parent.glob('logs/*/inventory*.json'))
        candidates=sorted(set(p for p in candidates if p.is_file()),key=lambda p:p.stat().st_mtime,reverse=True)[:40]
    inventory=None
    for path in candidates:
        if not path.is_file():continue
        try:
            raw=json.loads(command(['ansible-inventory','-i',str(path),'--list']))
            cover_inventory(raw,nodes);inventory=path;break
        except ValueError:
            if requested:raise
    if inventory is None:raise ValueError('未找到覆盖全部实时K8s节点的inventory，请填写完整节点清单；不会仅分发到gpu-workers的部分节点')
    return dict(playbook=str(playbook),playbook_sha256=sha(playbook),inventory=str(inventory),inventory_sha256=sha(inventory),nodes=nodes,node_count=len(nodes),nodes_hash=fingerprint(nodes))


def verification_play(stage,image,prepare=True):
    meta="{{ lookup('file', '"+str(stage/'controller.json')+"') | from_json }}"
    reference=pinned(image,'{{ platform_meta.digest }}')
    pattern="'^' ~ (platform_reference | regex_escape) ~ '\\s'"
    matches="platform_images.stdout_lines | select('match', "+pattern+") | list"
    tasks=[]
    if prepare:
        tasks.extend([dict(name='Register immutable digest reference',command=dict(argv=['ctr','-n','k8s.io','images','tag','--force',image,reference])),
            dict(name='Verify transferred archive SHA256',command=dict(argv=['sha256sum',str(Path('/opt/image-cache')/('platform-'+stage.name+'.tar'))]),register='platform_archive_hash',changed_when=False),
            {'name':'Require exact archive SHA256','assert':{'that':['platform_archive_hash.stdout.split()[0] == platform_meta.archive_sha256']}}])
    tasks.extend([dict(name='Read cached containerd digest',command=dict(argv=['ctr','-n','k8s.io','images','ls']),register='platform_images',changed_when=False),
        {'name':'Require exact cached image digest','assert':{'that':['('+matches+' | length) == 1','('+matches+' | first).split()[2] == platform_meta.digest']}},
        dict(name='Save verified node result',copy=dict(dest=str(stage/'results')+'/{{ inventory_hostname }}.json',mode='0600',
            content="{{ {'node':inventory_hostname,'uid':platform_node_uid,'digest':platform_meta.digest,'archive_sha256':platform_meta.archive_sha256,'ok':true} | to_json }}"),delegate_to='localhost')])
    return dict(name='Verify every cluster node',hosts='gpu-workers',gather_facts=False,vars=dict(platform_meta=meta,platform_reference=reference),tasks=tasks)


def build_playbook(plays,stage,image):
    stage=Path(stage);selected=copy.deepcopy([p for p in plays if p.get('name') in ('Prepare Triton archive on controller','Inspect Triton image state on GPU nodes','Rsync Triton archive and start import','Wait for Triton import to finish')])
    if len(selected)!=4:raise ValueError('预分发流程不完整')
    archive='platform-'+stage.name+'.tar';pulls=0
    for play in selected:
        play.setdefault('vars',{}).update(dict(triton_image_ref=image,triton_archive=archive,controller_cache_dir=str(stage/'artifacts'),remote_cache_dir='/opt/image-cache',
            containerd_namespace='k8s.io',force_pull=True,force_export=True,sync_force=True,import_force=True,cleanup_remote_archive=False,image_copy_serial=4))
        if 'Rsync' in play['name']:play['serial']=4
        if play['hosts']=='localhost':
            for task in play['tasks']:
                if 'images pull' in str(task.get('command','')):
                    task['command']=dict(argv=['ctr','-n','k8s.io','images','pull','--hosts-dir',str(stage/'hosts'),image]);pulls+=1
            matching="platform_controller_images.stdout_lines | select('match', '^' ~ ('"+image+"' | regex_escape) ~ '\\s') | list | first"
            play['tasks'].extend([dict(name='Read controller image digest',command=dict(argv=['ctr','-n','k8s.io','images','ls']),register='platform_controller_images',changed_when=False),
                dict(name='Hash exported archive',command=dict(argv=['sha256sum',str(stage/'artifacts'/archive)]),register='platform_controller_hash',changed_when=False),
                dict(name='Save controller receipt',copy=dict(dest=str(stage/'controller.json'),mode='0600',content="{{ {'image':'"+image+"','digest':("+matching+").split()[2],'archive_sha256':platform_controller_hash.stdout.split()[0]} | to_json }}"))])
    if pulls!=1:raise ValueError('无法安全定位控制机镜像拉取任务')
    ping=dict(name='Check all nodes before pulling',hosts='gpu-workers',gather_facts=False,tasks=[dict(name='Verify node connection',ping={}),dict(name='Verify node containerd',command=dict(argv=['ctr','version']),changed_when=False)])
    result=[ping]+selected+[verification_play(stage,image)]
    for play in result:
        for task in play['tasks']:task['no_log']=True
    return result


def verify_results(folder,nodes,receipt):
    results=[]
    for node in nodes:
        path=Path(folder)/(node['name']+'.json')
        if not path.is_file():raise ValueError('镜像预分发缺少节点结果：'+node['name'])
        result=json.loads(path.read_text())
        if not result.get('ok') or result.get('node')!=node['name'] or result.get('digest')!=receipt['digest'] or result.get('archive_sha256')!=receipt['archive_sha256']:raise ValueError('节点镜像摘要或传输SHA256未通过：'+node['name'])
        if result.get('uid')!=node['uid']:raise ValueError('节点身份已变化：'+node['name'])
        results.append(dict(result,uid=node['uid']))
    return results


def prepare_hosts(stage,target,image):
    registry=image.split('/')[0] if '/' in image and ('.' in image.split('/')[0] or ':' in image.split('/')[0]) else 'docker.io'
    directory=private(stage/'hosts'/registry);original=Path('/etc/containerd/certs.d')/registry
    if original.is_dir():
        for path in original.iterdir():
            if path.is_file() and not path.is_symlink():shutil.copyfile(str(path),str(directory/path.name));os.chmod(str(directory/path.name),0o600)
    hosts=directory/'hosts.toml';content=hosts.read_text() if hosts.is_file() else 'server = "https://'+registry+'"\n[host."https://'+registry+'"]\ncapabilities = ["pull", "resolve"]\n'
    if 'authorization' not in content.lower():
        workload=json.loads(command(['kubectl','get',target['workload_kind'],target['workload'],'-n',target['namespace'],'-o','json']))
        header=None
        for secret in workload['spec']['template']['spec'].get('imagePullSecrets',[]):
            value=json.loads(command(['kubectl','get','secret',secret['name'],'-n',target['namespace'],'-o','json']))
            encoded=value.get('data',{}).get('.dockerconfigjson')
            if not encoded:continue
            auths=json.loads(base64.b64decode(encoded).decode()).get('auths',{})
            for name,auth in auths.items():
                normalized=name.replace('https://','').replace('http://','').rstrip('/')
                if normalized.endswith('/v1'):normalized=normalized[:-3]
                if normalized!=registry:continue
                header=auth.get('auth') or base64.b64encode((auth.get('username','')+':'+auth.get('password','')).encode()).decode()
        if header:
            base64.b64decode(header,validate=True)
            urls=[value for value in re.findall(r'\[host\."([^"]+)"\]',content) if urlsplit(value).netloc==registry]
            if len(urls)!=1:raise ValueError('无法唯一识别对应仓库的containerd hosts.toml镜像源，拒绝向其他源发送凭据')
            section='[host."'+urls[0]+'".header]'
            if section in content:content=content.replace(section,section+'\nauthorization = ["Basic '+header+'"]',1)
            else:content+='\n'+section+'\nauthorization = ["Basic '+header+'"]\n'
    hosts.write_text(content);os.chmod(str(hosts),0o600)


def alive(state):
    try:
        fields=(Path('/proc')/str(state['pid'])/'stat').read_text().rsplit(')',1)[1].split()
        return fields[0]!='Z' and fields[19]==state['started']
    except (OSError,KeyError):return False


def prepare(payload):
    import fcntl
    job=job_folder(payload['job_id']);info=payload['preflight'];UUID(payload['batch_id'])
    key=fingerprint([payload['batch_id'],payload['image'],info]);stage=private(root()/'preloads'/key)
    with (stage/'launch.lock').open('a+') as lock:
        fcntl.flock(lock.fileno(),fcntl.LOCK_EX)
        result=stage/'result.json';state=stage/'state.json'
        if result.is_file():
            value=json.loads(result.read_text())
            if value.get('prepared'):
                if fingerprint(cluster_nodes())!=info['nodes_hash']:raise ValueError('预分发后K8s节点名单发生变化，请重新准备')
                write(job/'receipt.json',value);return value
            if not payload.get('retry'):raise ValueError(value.get('error','Ansible预分发失败'))
        if state.is_file() and alive(json.loads(state.read_text())):return dict(pending=True,phase='ansible_preload')
        if state.is_file() and not payload.get('retry'):raise ValueError('Ansible预分发被中断，请重试准备')
        current=preflight(payload['target'])
        if {k:v for k,v in current.items() if k!='nodes'}!=info:raise ValueError('剧本、inventory或实时节点名单已变化，请重新检查')
        source=globals().get('PLATFORM_SOURCE')
        if not source:raise ValueError('缺少远程Ansible执行器源码')
        (stage/'runner.py').write_bytes(source);os.chmod(str(stage/'runner.py'),0o600)
        if result.exists():result.unlink()
        write(stage/'input.json',dict(payload,preflight=current,cache_key=key))
        code=[sys.executable,str(stage/'runner.py'),base64.b64encode(json.dumps(dict(action='run',cache_key=key)).encode()).decode()]
        with (stage/'runner.log').open('ab') as output:process=subprocess.Popen(code,stdin=subprocess.DEVNULL,stdout=output,stderr=output,start_new_session=True)
        try:started=(Path('/proc')/str(process.pid)/'stat').read_text().rsplit(')',1)[1].split()[19]
        except OSError:started=''
        write(state,dict(pid=process.pid,started=started));return dict(pending=True,phase='ansible_preload')


def run(cache_key):
    import fcntl
    stage=private(root()/'preloads'/cache_key);payload=json.loads((stage/'input.json').read_text());info=payload['preflight']
    try:
        with (root()/'preload.lock').open('a+') as lock:
            fcntl.flock(lock.fileno(),fcntl.LOCK_EX)
            if preflight(payload['target'])!=info:raise ValueError('预分发前配置或节点名单已变化')
            raw=json.loads(command(['ansible-inventory','-i',info['inventory'],'--list']));hosts=cover_inventory(raw,info['nodes'])
            write(stage/'inventory.json',{'all':{'children':{'gpu-workers':{'hosts':hosts}}}})
            attempt=private(stage/'attempts'/uuid4().hex)
            private(attempt/'artifacts');private(attempt/'results');prepare_hosts(attempt,payload['target'],payload['image'])
            write(attempt/'playbook.json',build_playbook(load_plays(info['playbook']),attempt,payload['image']))
            environment=dict(os.environ,ANSIBLE_NO_LOG='True',ANSIBLE_DISPLAY_ARGS_TO_STDOUT='False',ANSIBLE_HOST_KEY_CHECKING='True')
            with (attempt/'ansible.log').open('wb') as output:
                result=subprocess.run(['ansible-playbook','-i',str(stage/'inventory.json'),str(attempt/'playbook.json'),'-f','4'],cwd=str(Path(info['playbook']).parent),env=environment,stdout=output,stderr=output,timeout=14000)
            if result.returncode:raise ValueError('Ansible预分发未全部成功')
            if cluster_nodes()!=info['nodes']:raise ValueError('预分发期间节点名单发生变化')
            controller=json.loads((attempt/'controller.json').read_text())
            if not re.fullmatch(r'sha256:[a-f0-9]{64}',controller['digest']):raise ValueError('控制机镜像摘要无效')
            nodes=verify_results(attempt/'results',info['nodes'],controller)
            write(stage/'controller.json',controller)
            write(stage/'result.json',dict(controller,prepared=True,nodes=nodes,node_count=len(nodes),preflight=info,cache_key=cache_key,pinned_image=pinned(payload['image'],controller['digest']),log_path=str(attempt/'ansible.log')))
    except Exception:write(stage/'result.json',dict(prepared=False,error='Ansible预分发失败、超时或节点/摘要核验未通过；请查看控制服务器私有操作日志'))


def verify(payload):
    job=job_folder(payload['job_id']);receipt=json.loads((job/'receipt.json').read_text());info=receipt['preflight']
    if receipt['image']!=payload['image'] or receipt['digest']!=payload['digest'] or cluster_nodes()!=info['nodes']:raise ValueError('K8s预分发镜像或节点名单已变化，请重新准备')
    if not re.fullmatch(r'[a-f0-9]{64}',receipt['cache_key']):raise ValueError('预分发收据无效')
    stage=root()/'preloads'/receipt['cache_key'];check=private(job/('verify-'+uuid4().hex));private(check/'results')
    shutil.copyfile(str(stage/'controller.json'),str(check/'controller.json'))
    play=verification_play(check,receipt['image'],prepare=False)
    for task in play['tasks']:task['no_log']=True
    write(check/'verify.json',[play])
    command(['ansible-playbook','-i',str(stage/'inventory.json'),str(check/'verify.json'),'-f','4'],240)
    results=verify_results(check/'results',info['nodes'],receipt)
    return dict(ok=True,node_count=len(results),nodes=results)


def dispatch(payload):
    if payload['action']=='preflight':return preflight(payload['target'])
    if payload['action']=='syntax_check':
        info=preflight(payload['target']);plays=build_playbook(load_plays(info['playbook']),Path('/tmp/platform-image-syntax-only'),payload['image'])
        result=subprocess.run(['ansible-playbook','-i',info['inventory'],'--syntax-check','/dev/stdin'],input=json.dumps(plays),stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True,timeout=120)
        return dict(ok=result.returncode==0,summary=result.stdout.strip(),errors=[line for line in result.stderr.splitlines() if line.startswith('ERROR!')])
    if payload['action']=='prepare':return prepare(payload)
    if payload['action']=='run':
        if not re.fullmatch(r'[a-f0-9]{64}',payload['cache_key']):raise ValueError('预分发缓存ID无效')
        run(payload['cache_key']);return dict(ok=True)
    if payload['action']=='verify':return verify(payload)
    raise ValueError('未知Ansible预分发动作')


if __name__=='__main__':
    try:print(json.dumps(dict(success=True,result=dispatch(json.loads(base64.b64decode(sys.argv[1])))),ensure_ascii=False))
    except Exception as error:print(json.dumps(dict(success=False,error=str(error)[-1500:]),ensure_ascii=False))
