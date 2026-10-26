// This private bridge receives only backend-generated training actions or a staged ZIP upload.
import { createRequire } from 'node:module';
import { dirname, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { isIP } from 'node:net';

const [entry, server] = process.argv.slice(2);
let client, transport, timer;
try {
  const input = JSON.parse(readFileSync(0, 'utf8'));
  const monitoring = input.executor === 'deployment_logs';
  const timeoutMs = monitoring ? 40000 : Math.max(330, Math.min(14400, Number.isInteger(input.timeout_seconds) ? input.timeout_seconds : 330)) * 1000;
  const require = createRequire(resolve(entry));
  const { Client } = await import(pathToFileURL(require.resolve('@modelcontextprotocol/sdk/client/index.js')).href);
  const { StdioClientTransport } = await import(pathToFileURL(require.resolve('@modelcontextprotocol/sdk/client/stdio.js')).href);
  let env = Object.fromEntries(Object.entries(process.env).filter(([key]) => key.startsWith('SSH_') || key === 'PREFER_TOML_CONFIG'));
  if (input.connection) {
    const c = input.connection;
    if (server !== 'platform_training' || !isIP(c.host) || c.host.includes('%') || !Number.isInteger(c.port) || c.port < 1 || c.port > 65535
      || !/^[a-zA-Z_][a-zA-Z0-9_.-]{0,63}$/.test(c.username) || typeof c.password !== 'string' || !c.password || c.password.includes('\0')) throw Error('Invalid connection');
    env = { SSH_ENV_PATH: resolve(entry, 'no-training-env'), SSH_CONFIG_PATH: resolve(entry, 'no-training-config'),
      SSH_SERVER_PLATFORM_TRAINING_HOST: c.host, SSH_SERVER_PLATFORM_TRAINING_PORT: String(c.port),
      SSH_SERVER_PLATFORM_TRAINING_USER: c.username, SSH_SERVER_PLATFORM_TRAINING_PASSWORD: c.password,
      SSH_SERVER_PLATFORM_TRAINING_DEFAULT_DIR: '/', SSH_SERVER_PLATFORM_TRAINING_PLATFORM: 'linux', SSH_SERVER_PLATFORM_TRAINING_MODE: 'unrestricted' };
  }
  env.MCP_SSH_MAX_OUTPUT_LENGTH = '1048576'; env.SSH_LOG_LEVEL = 'ERROR';
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
      const transfer = input.upload || input.download;
      result = await client.callTool({ name: input.upload ? 'ssh_upload' : 'ssh_download', arguments: { server, localPath: transfer.localPath, remotePath: transfer.remotePath } }, undefined, { timeout: timeoutMs - 40000 });
      if (result.isError) throw Error('Upload failed');
      const body = result.content?.filter(c => c.type === 'text').map(c => c.text).join('');
      let data; try { data = JSON.parse(body); } catch { data = {}; }
      const receipt = input.upload ? /^✅ File uploaded successfully/m : /^✅ File downloaded successfully/m;
      if (data.success === false || /"error"\s*:/.test(body) || !(data.success === true || receipt.test(body))) throw Error('File transfer failed');
      process.stdout.write(JSON.stringify({ success: true }));
    } else {
      const executor = monitoring ? 'deployment_logs_executor.py' : input.executor === 'image_preload' ? 'image_preload_executor.py' : input.executor === 'image_update' ? 'image_update_executor.py' : input.executor === 'model_deployment' ? 'model_deployment_executor.py' : input.executor === 'training_metrics' ? 'training_metrics.py' : input.executor === 'gpu_release' ? 'gpu_release_executor.py' : input.executor === 'validation' ? 'checkpoint_executor.py' : 'training_executor.py';
      const code = readFileSync(resolve(dirname(fileURLToPath(import.meta.url)), '../services/' + executor)).toString('base64');
      const payload = Buffer.from(JSON.stringify(input.payload)).toString('base64');
      const command = `python3 -c "import base64;PLATFORM_SOURCE=base64.b64decode('${code}');exec(PLATFORM_SOURCE)" '${payload}'`;
      result = await client.callTool({ name: 'ssh_execute', arguments: { server, command, timeout: monitoring ? 20000 : 300000 } }, undefined, { timeout: monitoring ? 25000 : 310000 });
      const data = JSON.parse(result.content?.filter(c => c.type === 'text').map(c => c.text).join(''));
      if (result.isError || typeof data.stdout !== 'string') throw Error('Remote execution failed');
      const response = JSON.parse(data.stdout.trim());
      process.stdout.write(JSON.stringify(response));
    }
  })(), new Promise((_, reject) => { timer = setTimeout(() => reject(Error('Timeout')), timeoutMs - 10000); })]);
} catch {
  process.stdout.write(JSON.stringify({ success: false, error: 'SSH/MCP通信失败，执行状态需要核对' }));
  process.exitCode = 1;
} finally {
  clearTimeout(timer);
  if (client) await client.close().catch(() => {});
  if (transport) await transport.close().catch(() => {});
}
