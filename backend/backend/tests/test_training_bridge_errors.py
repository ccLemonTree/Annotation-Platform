import json
import base64
import re
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace
from uuid import uuid4

import pytest


@pytest.mark.parametrize('mode,phase,reason', [
    ('connect', 'connect', 'transport'),
    ('timeout', 'ssh_execute', 'timeout'),
    ('mcp_error', 'ssh_execute', 'mcp_is_error'),
    ('bad_mcp', 'parse_mcp', 'invalid_mcp_json'),
    ('missing', 'parse_mcp', 'missing_stdout'),
    ('bad_remote', 'parse_remote', 'invalid_remote_json'),
    ('timeout_envelope', 'ssh_execute', 'timeout'),
    ('transfer', 'transfer', 'mcp_is_error'),
])
def test_bridge_safe_failure_classification(tmp_path, mode, phase, reason):
    node = shutil.which('node')
    if not node: pytest.skip('Node runtime required')
    sdk = tmp_path / 'node_modules/@modelcontextprotocol/sdk'; (sdk / 'client').mkdir(parents=True)
    (sdk / 'package.json').write_text(json.dumps({'type': 'module', 'exports': {'./client/index.js': './client/index.js', './client/stdio.js': './client/stdio.js'}}))
    replies = {'mcp_error': {'isError': True, 'content': [{'type': 'text', 'text': 'secret-must-not-leak'}]}, 'bad_mcp': {'content': [{'type': 'text', 'text': 'secret-must-not-leak'}]}, 'missing': {'content': [{'type': 'text', 'text': '{}'}]}, 'bad_remote': {'content': [{'type': 'text', 'text': json.dumps({'stdout': 'secret-must-not-leak'})}]}, 'timeout_envelope': {'content': [{'type': 'text', 'text': json.dumps({'stdout': '', 'error': 'Command timeout after 60000ms secret-must-not-leak'})}]}, 'transfer': {'isError': True}}
    call = "throw Object.assign(new Error('secret-must-not-leak'), {name: 'TimeoutError'})" if mode == 'timeout' else 'return ' + json.dumps(replies.get(mode, {}))
    connect = "throw new Error('secret-must-not-leak')" if mode == 'connect' else ''
    (sdk / 'client/index.js').write_text('export class Client {async connect(){' + connect + '} async close(){} async callTool(){' + call + '}}')
    (sdk / 'client/stdio.js').write_text('export class StdioClientTransport {async close(){}}')
    entry = tmp_path / 'entry.mjs'; entry.touch()
    bridge = Path(__file__).resolve().parents[1] / 'scripts/training_mcp_bridge.mjs'
    payload = {'upload': {'localPath': 'local', 'remotePath': '/remote'}} if mode == 'transfer' else {'payload': {'action': 'stage'}}
    result = subprocess.run([node, str(bridge), str(entry), 'fixture'], input=json.dumps(payload), capture_output=True, text=True, encoding='utf-8', timeout=10)
    value = json.loads(result.stdout)
    assert value['success'] is False
    assert phase in value['error'] and reason in value['error']
    assert 'secret-must-not-leak' not in result.stdout + result.stderr


def test_k8s_bridge_model_cache_probe_uses_file_lock(tmp_path, monkeypatch):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node runtime required')
    sdk = tmp_path / 'node_modules/@modelcontextprotocol/sdk'
    (sdk / 'client').mkdir(parents=True)
    (sdk / 'package.json').write_text(json.dumps({'type': 'module', 'exports': {
        './client/index.js': './client/index.js', './client/stdio.js': './client/stdio.js'}}))
    (sdk / 'client/index.js').write_text("""export class Client {
      async connect(){} async close(){}
      async callTool({arguments: args}){
        return {content:[{type:'text',text:JSON.stringify({stdout:JSON.stringify({command:args.command})})}]};
      }
    }""")
    (sdk / 'client/stdio.js').write_text('export class StdioClientTransport {async close(){}}')
    entry = tmp_path / 'entry.mjs'
    entry.touch()
    bridge = Path(__file__).resolve().parents[1] / 'scripts/training_mcp_bridge.mjs'
    run_id = str(uuid4())
    payload = dict(action='merge_cache_probe', run_id=run_id, config=dict(kind='wemm', output_subdir='train'),
        input_kind='base', files=[dict(path='config.json', bytes=2, sha256='a'*64)], digest='a'*64, tenant_id=1)
    response = subprocess.run([node, str(bridge), str(entry), 'fixture'],
        input=json.dumps(dict(executor='k8s_training', payload=payload)), capture_output=True,
        text=True, encoding='utf-8', timeout=10)
    assert response.returncode == 0, response.stdout + response.stderr
    command = json.loads(response.stdout)['command']
    source = base64.b64decode(re.search(r"b64decode\('([^']+)'\)", command)[1])
    executor = {'__name__': 'isolated_executor'}
    exec(source, executor)
    run = tmp_path / run_id
    run.mkdir()
    locks = []
    monkeypatch.setitem(sys.modules, 'fcntl', SimpleNamespace(LOCK_EX=2, LOCK_UN=8,
        flock=lambda stream, flag: locks.append((Path(stream.name), flag))))
    executor.update(validate_config=lambda *args: None, mount_root=lambda: str(tmp_path),
        run_path=lambda *args, **kwargs: str(run))
    assert executor['execute'](payload) == dict(hit=False, upload_path=str(run / 'base.zip'))
    assert (tmp_path / 'chen/training/model-input-cache/1/.lock', 2) in locks
