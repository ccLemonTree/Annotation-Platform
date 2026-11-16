"""CPU ONNX and shared TensorRT policies; no real GPU or business data."""
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from services import training_executor as executor


def config(tmp_path, format='onnx'):
    return dict(kind='model_export', output={'format':format}, image='fixture:1', gpu_uuids=['GPU-legacy'],
                output_root=str(tmp_path), output_subdir='result', model_path='', parameters={}, mounts=[],
                environment={'CUDA_VISIBLE_DEVICES':'0'},shm_size_gb=4)


def test_onnx_precheck_never_queries_gpu_even_for_legacy_config(monkeypatch, tmp_path):
    monkeypatch.setattr(executor, 'docker', lambda *a, **k:SimpleNamespace(returncode=0,stdout='[{"Id":"sha256:test"}]'))
    def forbidden(*args, **kwargs):raise AssertionError('ONNX must not query GPU')
    monkeypatch.setattr(executor.subprocess, 'run', forbidden)
    result=executor.precheck({'config':config(tmp_path)})
    assert result['ok'] and result['gpus']==[]
    assert any(c['name']=='执行设备' and 'CPU' in c['detail'] for c in result['checks'])


@pytest.mark.parametrize('free,mig,ok', [('2000','Disabled',True),('0','Disabled',False),('2000','Enabled',False)])
def test_tensorrt_only_requires_free_memory_not_empty_gpu(monkeypatch, tmp_path, free, mig, ok):
    monkeypatch.setattr(executor, 'docker', lambda *a, **k:SimpleNamespace(returncode=0,stdout='[{"Id":"sha256:test"}]'))
    def probe(args, **kwargs):
        assert '--query-gpu=' in args[1], 'TensorRT must not gate on compute processes'
        return SimpleNamespace(returncode=0,stdout='0, GPU-legacy, T4, '+free+', '+mig)
    monkeypatch.setattr(executor.subprocess, 'run', probe)
    result=executor.precheck({'config':config(tmp_path,'tensorrt'),'pending_onnx':True})
    assert result['ok'] is ok


def test_training_still_rejects_busy_gpu(monkeypatch,tmp_path):
    monkeypatch.setattr(executor, 'docker', lambda *a, **k:SimpleNamespace(returncode=0,stdout='[{"Id":"sha256:test"}]'))
    monkeypatch.setattr(executor.subprocess, 'run',lambda args,**kw:SimpleNamespace(returncode=0,stdout=
        '0, GPU-legacy, T4, 2000, Disabled' if '--query-gpu=' in args[1] else 'GPU-legacy, 42'))
    value=config(tmp_path);value.update(kind='yolo26',dataset_path=str(tmp_path))
    assert not executor.precheck({'config':value})['ok']


def test_cpu_onnx_container_has_no_gpu_request_and_hides_cuda(monkeypatch,tmp_path):
    import contextlib
    id=str(uuid4());path=tmp_path/id;path.mkdir()
    monkeypatch.setattr(executor, 'run_directory',lambda payload:(path,'hash'))
    monkeypatch.setattr(executor, 'run_lock',lambda path:contextlib.nullcontext())
    monkeypatch.setattr(executor, 'container_state',lambda _:None)
    monkeypatch.setattr(executor, 'precheck',lambda p:dict(ok=True,image_digest='sha256:test'))
    calls=[];monkeypatch.setattr(executor,'docker',lambda args,**kw:calls.append(args))
    assert executor.execute(dict(action='start',run_id=id,config=config(tmp_path)))['status']=='running'
    args=calls[0];assert args[0]=='create' and '--gpus' not in args
    assert 'CUDA_VISIBLE_DEVICES=' in args and 'CUDA_VISIBLE_DEVICES=0' not in args
    assert 'NVIDIA_VISIBLE_DEVICES=void' in args


@pytest.mark.parametrize('format', ['onnx','tensorrt'])
def test_exports_do_not_reserve_legacy_source_or_target_gpu(format):
    from services.training_runs import reserved_gpus
    value=dict(kind='model_export',gpu_uuids=['GPU-target'],output={'format':format},
               onnx_stage={'config':{'gpu_uuids':['GPU-source']}})
    assert reserved_gpus(SimpleNamespace(config=value))==[]


def test_onnx_request_does_not_require_gpu_but_trt_and_merge_do():
    from schemas.model_output import ModelOutputCreate
    from pydantic import ValidationError
    assert ModelOutputCreate(format='onnx',source_path='/data/model.pt').gpu_uuid==''
    for format in ('tensorrt','merge'):
        with pytest.raises(ValidationError):ModelOutputCreate(format=format,source_path='/data/model.pt')


def test_cpu_onnx_manifest_never_probes_nvidia(monkeypatch,tmp_path):
    from services import model_output_runner as runner
    monkeypatch.setattr(runner,'Path',lambda path:tmp_path if path=='/output' else Path(path))
    monkeypatch.setattr(runner,'check',lambda c:{})
    monkeypatch.setattr(runner,'sources',lambda c:{})
    def export(c,out):
        path=out/'model.onnx';path.write_bytes(b'fixture');return path,{}
    monkeypatch.setattr(runner,'export_onnx',export)
    def forbidden(*args,**kwargs):raise AssertionError('CPU metadata must not probe GPU')
    monkeypatch.setattr(runner.subprocess,'run',forbidden)
    runner.run(config(tmp_path))
    result=json.loads((tmp_path/'result/result.json').read_text())
    assert result['gpu_uuid']=='' and result['gpu_name']==''


def test_yolo_onnx_export_selects_cpu(monkeypatch,tmp_path):
    import sys
    from services import model_output_runner as runner
    source=tmp_path/'model.pt';source.write_bytes(b'fixture')
    out=tmp_path/'result';out.mkdir();calls=[]
    class Model:
        def __init__(self,path):pass
        def export(self,**kwargs):
            calls.append(kwargs);path=out/'model.onnx';path.write_bytes(b'fixture');return path
    monkeypatch.setitem(sys.modules,'ultralytics',SimpleNamespace(YOLO=Model))
    monkeypatch.setattr(runner,'prepare_onnx',lambda *a,**k:{})
    from schemas.model_output import ModelOutputCreate
    options=ModelOutputCreate(format='onnx',source_path='/data/model.pt').model_dump()
    runner.export_onnx(dict(model_kind='yolo26',model_path=str(source),output=options),out)
    assert calls[0]['device']=='cpu' and calls[0]['half'] is False
