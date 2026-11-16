import assert from 'node:assert/strict';
import { connectionEnvironment } from './ssh_connection_env.mjs';
const target = { host: '10.0.0.1', port: 22, username: 'root', password: 'fixture-target-secret' };
const jump = { host: '192.0.2.1', port: 2222, username: 'gateway', password: 'fixture-jump-secret' };
for (const name of ['platform_gpu', 'platform_training', 'platform_terminal']) {
  const original = JSON.stringify(process.env), prefix = `SSH_SERVER_${name.toUpperCase()}_`;
  const direct = connectionEnvironment('/fixture/index.js', name, target, 'readonly');
  assert.equal(direct[prefix + 'PASSWORD'], target.password);
  assert.equal(direct[prefix + 'PROXYJUMP'], undefined);
  const env = connectionEnvironment('/fixture/index.js', name, { ...target, jump }, 'unrestricted');
  assert.equal(env[prefix + 'PROXYJUMP'], 'platform_jump');
  assert.equal(env.SSH_SERVER_PLATFORM_JUMP_HOST, jump.host);
  assert.equal(env.SSH_SERVER_PLATFORM_JUMP_PASSWORD, jump.password);
  assert.equal(env.SSH_SERVER_PLATFORM_JUMP_PORT, '2222');
  assert.equal(env[prefix + 'PASSWORD'], target.password);
  assert.equal(JSON.stringify(process.env), original);
  for (const invalid of [{ ...jump, host: 'host;evil' }, { ...jump, port: true }, { ...jump, password: '' }, { ...jump, jump }]) {
    assert.throws(() => connectionEnvironment('/fixture/index.js', name, { ...target, jump: invalid }, 'unrestricted'), /Invalid connection/);
  }
}
console.log('GPU/training/PTY dynamic environments: direct and one-hop routing, validation and parent-environment isolation passed');
