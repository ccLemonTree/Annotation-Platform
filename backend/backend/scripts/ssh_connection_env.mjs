// Build isolated ssh-manager child environments; credentials arrive only via stdin.
import { resolve } from 'node:path';
import { isIP } from 'node:net';

function validate(value, jump = false) {
  if (!value || typeof value !== 'object' || Array.isArray(value)
    || typeof value.host !== 'string' || !isIP(value.host) || value.host.includes('%')
    || !Number.isInteger(value.port) || value.port < 1 || value.port > 65535
    || typeof value.username !== 'string' || !/^[a-zA-Z_][a-zA-Z0-9_.-]{0,63}$/.test(value.username)
    || typeof value.password !== 'string' || !value.password.length || value.password.length > 1024 || value.password.includes('\0')
    || jump && Object.keys(value).some(key => !['host', 'port', 'username', 'password'].includes(key))) throw Error('Invalid connection');
}

export function connectionEnvironment(entry, name, connection, mode) {
  if (!['platform_gpu', 'platform_training', 'platform_terminal'].includes(name)) throw Error('Invalid connection');
  validate(connection);
  const env = { SSH_ENV_PATH: resolve(entry, 'no-platform-env'), SSH_CONFIG_PATH: resolve(entry, 'no-platform-config') };
  const add = (server, value) => {
    const prefix = `SSH_SERVER_${server.toUpperCase()}_`;
    Object.assign(env, { [prefix + 'HOST']: value.host, [prefix + 'PORT']: String(value.port),
      [prefix + 'USER']: value.username, [prefix + 'PASSWORD']: value.password,
      [prefix + 'DEFAULT_DIR']: '/', [prefix + 'PLATFORM']: 'linux', [prefix + 'MODE']: mode });
  };
  add(name, connection);
  if (connection.jump != null) {
    validate(connection.jump, true); add('platform_jump', connection.jump);
    env[`SSH_SERVER_${name.toUpperCase()}_PROXYJUMP`] = 'platform_jump';
  }
  return env;
}
