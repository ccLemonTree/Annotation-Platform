import hashlib,subprocess
from uuid import UUID
import pytest
from tests.test_training_runs import training_tables,spec


@pytest.mark.asyncio
@pytest.mark.parametrize('state',['draft','completed','failed','canceled','running','starting','stopping','unknown'])
async def test_delete_only_inactive_records_never_remote_files(client,auth_headers,test_db,monkeypatch,state):
    from services import training_runs
    from models.training_run import TrainingRun
    async def forbidden(*args,**kwargs):raise AssertionError('Delete must not touch remote files or containers')
    monkeypatch.setattr(training_runs,'remote',forbidden)
    run=client.post('/api/training/runs',headers=auth_headers,json=spec().model_dump(mode='json')).json()
    row=await test_db.get(TrainingRun,UUID(run['id']));row.status=state;await test_db.commit()
    response=client.delete('/api/training/runs/'+run['id'],headers=auth_headers)
    assert response.status_code==(409 if state in training_runs.ACTIVE else 204),response.text
    assert client.get('/api/training/runs/'+run['id'],headers=auth_headers).status_code==(200 if state in training_runs.ACTIVE else 404)


@pytest.mark.asyncio
async def test_delete_refuses_unreleased_gpu_hold_and_requires_admin(client,auth_headers,test_db,test_user):
    from models.training_run import TrainingGpuHold
    run=client.post('/api/training/runs',headers=auth_headers,json=spec().model_dump(mode='json')).json();url='/api/training/runs/'+run['id']
    assert client.delete(url).status_code==401
    test_user.role='annotator';await test_db.commit()
    assert client.delete(url,headers=auth_headers).status_code==403
    test_user.role='admin';test_db.add(TrainingGpuHold(gpu_uuid='GPU-test',run_id=UUID(run['id'])));await test_db.commit()
    assert client.delete(url,headers=auth_headers).status_code==409


def test_artifact_download_checks_remote_hash_and_cleans_local_copy(client,auth_headers,monkeypatch,tmp_path):
    from services import training_runs
    content=b'checkpoint-content';local=[]
    async def remote(db,run,action,**values):
        assert action=='artifact' and values['path']=='weights/best.pt'
        return dict(path=run.output_dir+'/'+run.config['output_subdir']+'/weights/best.pt',bytes=len(content),sha256=hashlib.sha256(content).hexdigest())
    def bridge(server,input):
        from pathlib import Path
        dest=Path(input['download']['localPath']);dest.write_bytes(content);local.append(dest)
        return {}
    monkeypatch.setattr(training_runs,'remote',remote);monkeypatch.setattr(training_runs,'bridge',bridge)
    run=client.post('/api/training/runs',headers=auth_headers,json=spec().model_dump(mode='json')).json()
    response=client.get('/api/training/runs/'+run['id']+'/artifacts/download',params={'path':'weights/best.pt'},headers=auth_headers)
    assert response.status_code==200,response.text
    assert response.content==content and 'best.pt' in response.headers['content-disposition']
    assert local and not local[0].exists()


def test_artifact_path_rejects_traversal_and_symlinks(tmp_path):
    from services.training_executor import artifact_file
    root=tmp_path/'output';root.mkdir();(root/'best.pt').write_bytes(b'weights')
    assert artifact_file(root,'best.pt')==root/'best.pt'
    for value in ['../secret','/etc/passwd','a/../../secret','a\\secret','']:
        with pytest.raises(ValueError):artifact_file(root,value)
    try:(root/'link').symlink_to(tmp_path,target_is_directory=True)
    except OSError:pytest.skip('Windows symlink privilege unavailable')
    with pytest.raises(ValueError):artifact_file(root,'link/output/best.pt')


def test_terminal_log_normalizes_progress_ansi_and_cursor_updates():
    from services.training_logs import normalize_log
    text='\x1b[32mEpoch 1\x1b[0m\n10%\r50%\r100%\nWarning: example\nTraceback: real error\n'
    result=normalize_log(text)
    assert result.splitlines()==['Epoch 1','100%','Warning: example','Traceback: real error']
    assert normalize_log('train 10%\nval 20%\x1b[1A\rtrain 90%\x1b[K\n')=='train 90%\nval 20%'


def test_docker_log_stream_order_is_not_stdout_then_stderr(monkeypatch):
    from services import training_executor as executor
    monkeypatch.setattr(executor,'container_state',lambda run:dict())
    def run(args,**kwargs):
        assert kwargs['stderr']==subprocess.STDOUT
        return subprocess.CompletedProcess(args,0,stdout=b'out1\nerr1\nout2\n')
    monkeypatch.setattr(executor.subprocess,'run',run)
    result=executor.execute(dict(action='logs',config={},run_id='33333333-3333-4333-8333-333333333333'))
    assert result['text']=='out1\nerr1\nout2\n'
