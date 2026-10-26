"""Execute the shipped remote snippet against Python 3.6 subprocess arguments."""
import ast
from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import re
from types import SimpleNamespace
import pytest
from tests.test_gpu_resources import sample


def remote_script():
    source=(Path(__file__).resolve().parents[1]/'scripts/gpu_mcp_bridge.mjs').read_text(encoding='utf-8')
    declaration=source.split('const script = ',1)[1].split('const command = ',1)[0]
    return ''.join(ast.literal_eval(part) for part in re.findall(r"'(?:[^'\\]|\\.)*'",declaration))


@pytest.mark.parametrize('gpu_code,process_code',[(0,0),(0,1),(1,0)])
def test_remote_collection_uses_python36_subprocess_api(monkeypatch,gpu_code,process_code):
    import subprocess
    from services.gpu_resources import parse_sample
    calls=[];expected=sample(gpu_code=gpu_code,process_code=process_code)
    def run(args,stdout=None,stderr=None,universal_newlines=False,timeout=None):
        assert stdout==subprocess.PIPE and stderr==subprocess.PIPE
        assert universal_newlines is True and timeout==10
        assert args[0]=='nvidia-smi' and args[-1]=='--format=csv,noheader,nounits'
        calls.append(args)
        gpu=args[1].startswith('--query-gpu=')
        return SimpleNamespace(returncode=expected['gpu_code' if gpu else 'process_code'],stdout=expected['gpus' if gpu else 'processes'])
    monkeypatch.setattr(subprocess,'run',run)
    output=StringIO()
    with redirect_stdout(output):exec(compile(remote_script(),'remote-gpu.py','exec'),{})
    actual=json.loads(output.getvalue())
    assert actual==expected and len(calls)==2
    if gpu_code:
        with pytest.raises(ValueError,match='GPU query failed'):parse_sample(actual)
    else:
        parsed=parse_sample(actual)
        assert len(parsed['gpus'])==2 and parsed['processes_complete']==(process_code==0)
