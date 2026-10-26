// Local SSH fixture: tests the real MCP bridge without touching a remote host.
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { generateKeyPairSync, createHash } from 'node:crypto';
import { createRequire } from 'node:module';
import { readFileSync, existsSync } from 'node:fs';
import { homedir } from 'node:os';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const entry = resolve(process.argv[2]);
const require = createRequire(entry);
const { Server } = require('ssh2');
const password = 'fixture-only GPU 密码\n"$`\\';
const { privateKey } = generateKeyPairSync('rsa', { modulusLength: 2048, privateKeyEncoding: { type: 'pkcs1', format: 'pem' }, publicKeyEncoding: { type: 'pkcs1', format: 'pem' } });
const connections = new Set();
let fixedQueries = 0, authenticated = 0;
const config = resolve(homedir(), '.codex/ssh-config.toml');
const fingerprint = () => existsSync(config) ? createHash('sha256').update(readFileSync(config)).digest('hex') : null;
const before = fingerprint();
const server = new Server({ hostKeys: [privateKey] }, connection => {
  connections.add(connection);
  connection.on('close', () => connections.delete(connection));
  connection.on('error', () => {});
  connection.on('authentication', context => {
    if (context.method === 'password' && context.username === 'gpu_test' && context.password === password) { authenticated++; context.accept(); }
    else context.reject(['password']);
  });
  connection.on('ready', () => connection.on('session', accept => {
    const session = accept();
    session.on('exec', (acceptCommand, rejectCommand, info) => {
      if (!info.command.includes('nvidia-smi') || !info.command.includes('--query-compute-apps=') || !info.command.includes('--query-gpu=')) return rejectCommand();
      fixedQueries++;
      const channel = acceptCommand();
      channel.write(JSON.stringify({ gpu_code: 0, process_code: 0,
        gpus: '0, GPU-fixture, NVIDIA T4, 550.54, 15360, 1024, 14336, 10, 40, 20, 70, [N/A]\n', processes: '' }));
      channel.exit(0); channel.end();
    });
  }));
});
await new Promise((resolveReady, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolveReady); });
const port = server.address().port;
const bridge = resolve(dirname(fileURLToPath(import.meta.url)), '../backend/backend/scripts/gpu_mcp_bridge.mjs');
async function collect(pass) {
  const args = [bridge, entry, 'platform_gpu', '--connection-stdin'];
  assert.ok(!args.join(' ').includes(password));
  const child = spawn(process.execPath, args, { windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'] });
  let stdout = '', stderr = '';
  child.stdout.on('data', chunk => { stdout += chunk; }); child.stderr.on('data', chunk => { stderr += chunk; });
  child.stdin.on('error', () => {});
  child.stdin.end(JSON.stringify({ host: '127.0.0.1', port, username: 'gpu_test', password: pass }));
  const timer = setTimeout(() => child.kill(), 45000);
  try {
    const code = await new Promise((finish, reject) => { child.once('error', reject); child.once('close', finish); });
    assert.ok(!stdout.includes(password) && !stderr.includes(password), 'Credential leaked by MCP bridge');
    return { code, stdout };
  } finally { clearTimeout(timer); }
}
try {
  const good = await collect(password), bad = await collect('incorrect-fixture-password');
  assert.equal(good.code, 0, 'Correct password failed through real MCP');
  assert.equal(JSON.parse(good.stdout).gpu_code, 0);
  assert.notEqual(bad.code, 0, 'Wrong password was accepted');
  assert.equal(fixedQueries, 1); assert.equal(authenticated, 1);
  assert.equal(fingerprint(), before, 'Global SSH configuration changed');
  console.log(JSON.stringify({ correct_password: true, wrong_password_rejected: true, fixed_gpu_queries: fixedQueries,
    credentials_in_output: false, global_ssh_configuration_unchanged: true }));
} finally {
  for (const connection of connections) connection.end();
  await new Promise(done => server.close(done));
}
