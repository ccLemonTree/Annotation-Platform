// This private bridge receives only backend-generated training actions or a staged ZIP upload.
import { createRequire } from 'node:module';
import { dirname, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { connectionEnvironment } from './ssh_connection_env.mjs';

const [entry, server] = process.argv.slice(2);
let client, transport, timer, phase = 'connect', reason = 'transport';
try {
  const input = JSON.parse(readFileSync(0, 'utf8'));
  const monitoring = input.executor === 'deployment_logs';
  const timeoutMs = monitoring ? 40000 : Math.max(330, Math.min(14400, Number.isInteger(input.timeout_seconds) ? input.timeout_seconds : 330)) * 1000;
  const require = createRequire(resolve(entry));
  const { Client } = await import(pathToFileURL(require.resolve('@modelcontextprotocol/sdk/client/index.js')).href);
  const { StdioClientTransport } = await import(pathToFileURL(require.resolve('@modelcontextprotocol/sdk/client/stdio.js')).href);
  let env = Object.fromEntries(Object.entries(process.env).filter(([key]) => key.startsWith('SSH_') || key === 'PREFER_TOML_CONFIG'));
  if (input.connection) {
    if (server !== 'platform_training') throw Error('Invalid connection');
    env = connectionEnvironment(entry,server,input.connection,'unrestricted');
  }
  env.MCP_SSH_MAX_OUTPUT_LENGTH = input.executor === 'k8s_training' && input.payload?.action === 'metric_files' ? '16777216' : '1048576'; env.SSH_LOG_LEVEL = 'ERROR';
  transport = new StdioClientTransport({ command: process.execPath, args: [resolve(entry)], cwd: dirname(dirname(resolve(entry))), stderr: 'pipe', env });
  transport.stderr?.on('data', () => {});
  client = new Client({ name: 'data-platform-training', version: '1.0.0' });
  await Promise.race([(async () => {
    await client.connect(transport);
    let result;
    if (input.list_servers) {
      result = await client.callTool({ name: 'ssh_list_servers', arguments: {} });
      if (result.isError) throw Error('Server list failed');
      const servers = JSON.parse(result.content?.filter(c => c.type === 'text').map(c => c.text).join(''));
      if (!Array.isArray(servers)) throw Error('Invalid server list');
      process.stdout.write(JSON.stringify({ success: true, result: servers.map(({ name, host, user, port, group, description }) => ({ name, host, user, port, group, description })) }));
    } else if (input.upload || input.download) {
      phase = 'transfer';
      const transfer = input.upload || input.download;
      result = await client.callTool({ name: input.upload ? 'ssh_upload' : 'ssh_download', arguments: { server, localPath: transfer.localPath, remotePath: transfer.remotePath } }, undefined, { timeout: timeoutMs - 40000 });
      if (result.isError) { reason = 'mcp_is_error'; throw Error('Upload failed'); }
      const body = result.content?.filter(c => c.type === 'text').map(c => c.text).join('');
      let data; try { data = JSON.parse(body); } catch { data = {}; }
      const receipt = input.upload ? /^✅ File uploaded successfully/m : /^✅ File downloaded successfully/m;
      if (data.success === false || /"error"\s*:/.test(body) || !(data.success === true || receipt.test(body))) { reason = 'mcp_is_error'; throw Error('File transfer failed'); }
      process.stdout.write(JSON.stringify({ success: true }));
    } else {
      phase = 'ssh_execute';
      const executor = input.executor === 'vector_test' ? 'vector_test_executor.py' : input.executor === 'k8s_training' ? 'k8s_training_executor.py' : monitoring ? 'deployment_logs_executor.py' : input.executor === 'image_preload' ? 'image_preload_executor.py' : input.executor === 'image_update' ? 'image_update_executor.py' : input.executor === 'model_deployment' ? 'model_deployment_executor.py' : input.executor === 'training_metrics' ? 'training_metrics.py' : input.executor === 'gpu_release' ? 'gpu_release_executor.py' : input.executor === 'validation' ? 'checkpoint_executor.py' : 'training_executor.py';
      let source = readFileSync(resolve(dirname(fileURLToPath(import.meta.url)), '../services/' + executor), 'utf8');
      if (input.executor === 'vector_test') {
        const bundle = readFileSync(resolve(dirname(fileURLToPath(import.meta.url)), '../services/model_deployment_executor.py'), 'utf8');
        source = bundle.slice(0,bundle.lastIndexOf("\nif __name__")) + '\n' + source;
      }
      if (input.executor === 'k8s_training' && ['merge_cache_probe','merge_cache_publish'].includes(input.payload.action)) {
        const cacheCode = readFileSync(resolve(dirname(fileURLToPath(import.meta.url)), '../services/training_executor.py'), 'utf8');
        const marker = "if __name__ == '__main__':";
        if (!source.includes(marker)) throw Error('K8s executor entry point missing');
        const start = cacheCode.indexOf('def merge_cache(');
        const end = cacheCode.indexOf('def repository_metadata(', start);
        if (start < 0 || end < 0) throw Error('Model cache executor missing');
        const functions = ['safe_child', 'artifacts', 'artifact_file'].map(name => {
          const from = cacheCode.indexOf('def ' + name + '(');
          if (from < 0) throw Error('Model cache dependency missing');
          const to = cacheCode.indexOf('\ndef ', from);
          return cacheCode.slice(from, to);
        });
        const program = 'import errno, os, shutil, stat, tempfile, zipfile\nfrom pathlib import Path, PurePosixPath\nfile_hash = cache_file_hash\n' + functions.join('\n') + '\n' + cacheCode.slice(start, end);
        source = source.replace(marker, () => `MODEL_CACHE_SOURCE = ${JSON.stringify(program)}\n${marker}`);
      }
      const code = Buffer.from(source).toString('base64');
      const payload = Buffer.from(JSON.stringify(input.payload)).toString('base64');
      const python = input.executor === 'k8s_training' ? '/usr/local/bin/python3' : 'python3';
      const command = `${python} -c "import base64;PLATFORM_SOURCE=base64.b64decode('${code}');exec(PLATFORM_SOURCE)" '${payload}'`;
      result = await client.callTool({ name: 'ssh_execute', arguments: { server, command, timeout: monitoring ? 20000 : 300000 } }, undefined, { timeout: monitoring ? 25000 : 310000 });
      if (result.isError) { reason = 'mcp_is_error'; throw Error('Remote execution failed'); }
      phase = 'parse_mcp'; reason = 'invalid_mcp_json';
      const data = JSON.parse(result.content?.filter(c => c.type === 'text').map(c => c.text).join(''));
      if (typeof data?.stdout !== 'string' || !data.stdout.trim()) {
        if (typeof data?.error === 'string' && /timeout|timed out/i.test(data.error)) { phase = 'ssh_execute'; reason = 'timeout'; }
        else reason = 'missing_stdout';
        throw Error('Missing stdout');
      }
      phase = 'parse_remote'; reason = 'invalid_remote_json';
      const response = JSON.parse(data.stdout.trim());
      process.stdout.write(JSON.stringify(response));
    }
  })(), new Promise((_, reject) => { timer = setTimeout(() => reject(Error('Timeout')), timeoutMs - 10000); })]);
} catch (error) {
  if (reason === 'transport' && (error?.name === 'TimeoutError' || /timeout|timed out/i.test(String(error?.message || '')))) reason = 'timeout';
  process.stdout.write(JSON.stringify({ success: false, error: `SSH/MCP执行失败，执行状态需要核对（阶段：${phase}，类型：${reason}）` }));
  process.exitCode = 1;
} finally {
  clearTimeout(timer);
  if (client) await client.close().catch(() => {});
  if (transport) await transport.close().catch(() => {});
}
