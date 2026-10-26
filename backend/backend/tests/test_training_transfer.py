"""Transfer limits and MCP receipts; no SSH or business files are used."""
import json
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace
import pytest


@pytest.mark.parametrize('text,success', [
    ('✅ File uploaded successfully\nServer: fixture', True),
    ('❌ Upload error: secret-must-not-leak', False),
    ('unrecognized response', False),
])
def test_upload_bridge_requires_positive_receipt(tmp_path,text,success):
    node=shutil.which('node')
    if not node:pytest.skip('Node runtime required')
    sdk=tmp_path/'node_modules/@modelcontextprotocol/sdk'
    (sdk/'client').mkdir(parents=True)
    (sdk/'package.json').write_text(json.dumps({'type':'module','exports':{
        './client/index.js':'./client/index.js','./client/stdio.js':'./client/stdio.js'}}))
    reply={'content':[{'type':'text','text':text}]}
    (sdk/'client/index.js').write_text('export class Client { async connect(){} async close(){} async callTool(){return '+json.dumps(reply)+';} }',encoding='utf-8')
    (sdk/'client/stdio.js').write_text('export class StdioClientTransport { async close(){} }')
    entry=tmp_path/'entry.mjs';entry.touch()
    bridge=Path(__file__).resolve().parents[1]/'scripts/training_mcp_bridge.mjs'
    result=subprocess.run([node,str(bridge),str(entry),'fixture'],input=json.dumps({'upload':{
        'localPath':str(tmp_path/'sample.zip'),'remotePath':'/fixture.zip'},'timeout_seconds':330}),
        capture_output=True,text=True,encoding='utf-8',timeout=10)
    assert json.loads(result.stdout)['success'] is success
    assert 'secret-must-not-leak' not in result.stdout


def test_large_upload_gets_size_based_timeout(tmp_path,monkeypatch):
    from services import training_runs
    entry=tmp_path/'entry.js';entry.touch()
    upload=tmp_path/'large.zip';upload.touch()
    original=Path.stat
    monkeypatch.setattr(Path,'stat',lambda self,*a,**kw:SimpleNamespace(st_size=2*1024**3) if self==upload else original(self,*a,**kw))
    monkeypatch.setattr(training_runs.settings,'GPU_SSH_MANAGER_ENTRY',str(entry))
    calls=[]
    def run(*args,**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(stdout='{"success":true}')
    monkeypatch.setattr(training_runs.subprocess,'run',run)
    training_runs.bridge('fixture',{'upload':{'localPath':str(upload),'remotePath':'/large.zip'}})
    assert 330<calls[0]['timeout']<=4*3600
    assert json.loads(calls[0]['input'])['timeout_seconds']==calls[0]['timeout']
