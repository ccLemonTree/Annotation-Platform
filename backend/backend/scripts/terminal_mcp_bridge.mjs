// NDJSON parent protocol; credentials arrive only on stdin and never on argv/logs.
import { createRequire } from 'node:module';
import { dirname, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { createInterface } from 'node:readline';
import { once } from 'node:events';
import { isIP } from 'node:net';

const entry = resolve(process.argv[2]);
let client, transport, stopping = false, initialized = false, chain = Promise.resolve();
const input = createInterface({ input: process.stdin, crlfDelay: Infinity });
const emit = async value => { if (!process.stdout.write(JSON.stringify(value) + '\n')) await once(process.stdout, 'drain'); };
async function stop() {
  if (stopping) return;
  stopping = true;
  if (client) await client.callTool({ name: 'ssh_terminal_close', arguments: {} }, undefined, { timeout: 2000 }).catch(() => {});
  if (client) await client.close().catch(() => {});
  if (transport) await transport.close().catch(() => {});
  process.exit(0);
}
async function call(name, args = {}) {
  const result = await client.callTool({ name, arguments: args }, undefined, { timeout: name === 'ssh_terminal_open' ? 40000 : 5000 });
  const value = JSON.parse(result.content.filter(row => row.type === 'text').map(row => row.text).join(''));
  if (result.isError) throw Object.assign(new Error('PTY failed'), { terminalCode: value.code });
  return value;
}
async function start(config) {
  const require = createRequire(entry);
  const { Client } = await import(pathToFileURL(require.resolve('@modelcontextprotocol/sdk/client/index.js')).href);
  const { StdioClientTransport } = await import(pathToFileURL(require.resolve('@modelcontextprotocol/sdk/client/stdio.js')).href);
  let env = Object.fromEntries(Object.entries(process.env).filter(([key]) => key.startsWith('SSH_') || key === 'PREFER_TOML_CONFIG'));
  let server = config.server;
  if (config.connection) {
    const c = config.connection;
    if (!isIP(c.host) || c.host.includes('%') || !Number.isInteger(c.port) || c.port < 1 || c.port > 65535
      || !/^[a-zA-Z_][a-zA-Z0-9_.-]{0,63}$/.test(c.username) || typeof c.password !== 'string' || !c.password || c.password.length > 1024 || c.password.includes('\0')) throw Error('Invalid connection');
    server = 'platform_terminal';
    env = { SSH_ENV_PATH: resolve(entry, 'no-terminal-env'), SSH_CONFIG_PATH: resolve(entry, 'no-terminal-config'),
      SSH_SERVER_PLATFORM_TERMINAL_HOST: c.host, SSH_SERVER_PLATFORM_TERMINAL_PORT: String(c.port),
      SSH_SERVER_PLATFORM_TERMINAL_USER: c.username, SSH_SERVER_PLATFORM_TERMINAL_PASSWORD: c.password,
      SSH_SERVER_PLATFORM_TERMINAL_PLATFORM: 'linux', SSH_SERVER_PLATFORM_TERMINAL_MODE: 'unrestricted' };
  }
  env.PLATFORM_SSH_TERMINAL_EXTENSION = resolve(dirname(fileURLToPath(import.meta.url)), 'terminal_mcp_extension.mjs');
  env.SSH_LOG_LEVEL = 'ERROR'; env.MCP_SSH_MAX_OUTPUT_LENGTH = '1048576';
  transport = new StdioClientTransport({ command: process.execPath, args: [entry], cwd: dirname(dirname(entry)), env, stderr: 'pipe' });
  transport.stderr?.on('data', () => {});
  client = new Client({ name: 'data-platform-ssh-terminal', version: '1.0.0' });
  await client.connect(transport);
  const tools = await client.listTools();
  if (!tools.tools.some(tool => tool.name === 'ssh_terminal_open')) throw Object.assign(new Error('Missing extension'), { terminalCode: 'extension_missing' });
  await call('ssh_terminal_open', { server, cols: config.cols, rows: config.rows });
  await emit({ event: 'ready' });
  void (async () => {
    try {
      while (!stopping) {
        const value = await call('ssh_terminal_read');
        if (value.data) await emit({ event: 'output', data: value.data });
        if (value.closed) { await emit({ event: 'closed', error: value.error }); await stop(); break; }
      }
    } catch { if (!stopping) { await emit({ event: 'error', code: 'transport' }); await stop(); } }
  })();
}
input.on('line', line => {
  chain = chain.then(async () => {
    if (line.length > 20000) throw Error('Frame too large');
    const message = JSON.parse(line);
    if (!initialized) { initialized = true; await start(message); return; }
    if (stopping) return;
    if (message.type === 'input') await call('ssh_terminal_write', { data: message.data });
    else if (message.type === 'resize') await call('ssh_terminal_resize', { cols: message.cols, rows: message.rows });
    else throw Error('Unknown frame');
  }).catch(async error => { await emit({ event: 'error', code: error.terminalCode || 'transport' }); await stop(); });
});
input.on('close', () => { void stop(); });
process.on('SIGTERM', () => { void stop(); });
process.stdout.on('error', () => { void stop(); });
