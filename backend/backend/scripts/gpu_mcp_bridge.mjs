// Only this fixed read-only command is exposed to the backend.
import { createRequire } from 'node:module';
import { dirname, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { readFileSync } from 'node:fs';
import { isIP } from 'node:net';

const [entry, server] = process.argv.slice(2);
let client, transport, timer;
try {
  const require = createRequire(resolve(entry));
  const { Client } = await import(pathToFileURL(require.resolve('@modelcontextprotocol/sdk/client/index.js')).href);
  const { StdioClientTransport } = await import(pathToFileURL(require.resolve('@modelcontextprotocol/sdk/client/stdio.js')).href);
  const script = 'import subprocess,json; fields="index,uuid,name,driver_version,memory.total,memory.used,memory.free,utilization.gpu,temperature.gpu,power.draw,power.limit,mig.mode.current"; '
    + 'gpu=subprocess.run(["nvidia-smi","--query-gpu="+fields,"--format=csv,noheader,nounits"],stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True,timeout=10); '
    + 'procs=subprocess.run(["nvidia-smi","--query-compute-apps=gpu_uuid,pid,process_name,used_gpu_memory","--format=csv,noheader,nounits"],stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True,timeout=10); '
    + 'print(json.dumps({"gpu_code":gpu.returncode,"gpus":gpu.stdout,"process_code":procs.returncode,"processes":procs.stdout}))';
  const command = `LC_ALL=C python3 -c '${script}'`;
  let environment = Object.fromEntries(Object.entries(process.env).filter(([key]) => key.startsWith('SSH_') || key === 'PREFER_TOML_CONFIG'));
  if (process.argv[4] === '--connection-stdin') {
    const input = readFileSync(0, 'utf8');
    if (input.length > 16384) throw new Error('Invalid connection');
    const connection = JSON.parse(input);
    if (server !== 'platform_gpu' || typeof connection.host !== 'string' || !isIP(connection.host) || connection.host.includes('%')
      || !Number.isInteger(connection.port) || connection.port < 1 || connection.port > 65535
      || typeof connection.username !== 'string' || !/^[a-zA-Z_][a-zA-Z0-9_.-]{0,63}$/.test(connection.username)
      || typeof connection.password !== 'string' || !connection.password.length || connection.password.length > 1024 || connection.password.includes('\0')) throw new Error('Invalid connection');
    // The entry is a file, so these children cannot exist: no global SSH config is loaded.
    environment = { SSH_ENV_PATH: resolve(entry, 'no-platform-env'), SSH_CONFIG_PATH: resolve(entry, 'no-platform-config'),
      SSH_SERVER_PLATFORM_GPU_HOST: connection.host, SSH_SERVER_PLATFORM_GPU_PORT: String(connection.port),
      SSH_SERVER_PLATFORM_GPU_USER: connection.username, SSH_SERVER_PLATFORM_GPU_PASSWORD: connection.password,
      SSH_SERVER_PLATFORM_GPU_DEFAULT_DIR: '/', SSH_SERVER_PLATFORM_GPU_PLATFORM: 'linux', SSH_SERVER_PLATFORM_GPU_MODE: 'readonly' };
  }
  environment.MCP_SSH_MAX_OUTPUT_LENGTH = '1048576';
  environment.SSH_LOG_LEVEL = 'ERROR';
  transport = new StdioClientTransport({ command: process.execPath, args: [resolve(entry)],
    cwd: dirname(dirname(resolve(entry))), stderr: 'pipe', maxBufferSize: 2 * 1024 * 1024,
    env: environment });
  // Drain diagnostics without forwarding connection details or credentials.
  transport.stderr?.on('data', () => {});
  client = new Client({ name: 'data-platform-gpu-monitor', version: '1.0.0' });
  await Promise.race([
    (async () => {
      await client.connect(transport);
      const result = await client.callTool({ name: 'ssh_execute', arguments: { server, command, timeout: 25000 } }, undefined, { timeout: 28000 });
      if (result.isError) throw new Error('MCP error');
      const text = result.content?.filter(item => item.type === 'text').map(item => item.text).join('');
      const response = JSON.parse(text);
      if (!response.success || response.code !== 0 || typeof response.stdout !== 'string' || response.stdout.length > 1024 * 1024) throw new Error('Remote query failed');
      const sample = JSON.parse(response.stdout);
      process.stdout.write(JSON.stringify(sample));
    })(),
    new Promise((_, reject) => { timer = setTimeout(() => reject(Object.assign(new Error('Timeout'), { code: 'GPU_TIMEOUT' })), 33000); }),
  ]);
} catch (error) {
  process.exitCode = error.code === 'GPU_TIMEOUT' || error.code === -32001 ? 2 : 1;
} finally {
  clearTimeout(timer);
  if (client) await client.close().catch(() => {});
  if (transport) await transport.close().catch(() => {});
}
