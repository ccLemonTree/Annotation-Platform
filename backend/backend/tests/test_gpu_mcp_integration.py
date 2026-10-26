"""Real ssh-manager MCP transport with an owned, local fake SSH endpoint."""
import json
import os
from pathlib import Path
import subprocess
import pytest


@pytest.mark.skipif(os.environ.get('GPU_MCP_INTEGRATION') != '1', reason='explicit local MCP/SSH integration required')
def test_password_connection_through_real_mcp():
    from config.settings import settings
    script = Path(__file__).resolve().parents[3] / 'scripts/verify_gpu_mcp.mjs'
    assert script.is_file(), '缺少动态密码连接的真实MCP验证'
    result = subprocess.run([settings.GPU_SSH_NODE_BINARY, str(script), settings.GPU_SSH_MANAGER_ENTRY],
        capture_output=True, text=True, encoding='utf-8', timeout=100)
    assert result.returncode == 0, 'Local MCP integration failed: ' + result.stdout + result.stderr
    evidence = json.loads(result.stdout)
    assert evidence['correct_password'] is True and evidence['wrong_password_rejected'] is True
    assert evidence['fixed_gpu_queries'] == 1 and evidence['credentials_in_output'] is False
