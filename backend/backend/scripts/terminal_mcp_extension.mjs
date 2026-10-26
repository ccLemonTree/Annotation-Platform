// Opt-in PTY tools, loaded only by the platform's dedicated ssh-manager child.
export function registerTerminalTools(server, { z, getConnection, getServerConfig, touch }) {
  let shell, pending = Buffer.alloc(0), closed = false, failure, wake;
  const reply = value => ({ content: [{ type: 'text', text: JSON.stringify(value) }] });
  const fail = (code, error) => ({ ...reply({ code, error }), isError: true });
  const finish = () => { closed = true; wake?.(); };
  const push = data => {
    pending = Buffer.concat([pending, Buffer.from(data)]);
    if (pending.length > 2 * 1024 * 1024) { failure = '终端输出缓冲已满，会话已关闭'; shell.destroy(); finish(); }
    else if (pending.length >= 512 * 1024) shell.pause();
    wake?.();
  };
  const register = (name, inputSchema, handler) => server.registerTool(name, {
    description: 'Platform-owned interactive SSH PTY: ' + name, inputSchema,
  }, async args => {
    try { return reply(await handler(args)); }
    catch { return fail('pty_error', 'SSH终端操作失败或连接已关闭'); }
  });
  // Raw terminal keystrokes cannot be safely subjected to command allow/deny regexes.
  server.registerTool('ssh_terminal_open', { description: 'Open one raw Linux PTY for this dedicated MCP process', inputSchema: {
    server: z.string().min(1).max(200), cols: z.number().int().min(2).max(400), rows: z.number().int().min(2).max(200),
  } }, async ({ server: name, cols, rows }) => {
    try {
      if (shell || closed) return fail('already_open', '此进程已有终端会话');
      const config = await getServerConfig(name);
      if (!config || config.mode && config.mode !== 'unrestricted') return fail('policy', '只读或受限制连接不能打开完整交互终端');
      if (config.platform && config.platform !== 'linux') return fail('platform', '当前交互终端仅支持Linux服务器');
      const ssh = await getConnection(name);
      shell = await ssh.requestShell({ term: 'xterm-256color', cols, rows, modes: { ECHO: 1 } });
      shell.on('data', push); shell.stderr.on('data', push);
      shell.on('close', finish); shell.on('error', () => { failure = 'SSH终端连接中断'; finish(); });
      return reply({ ok: true, cols, rows });
    } catch { return fail('connect', 'SSH终端连接失败，请核对服务器连接与凭据'); }
  });
  register('ssh_terminal_read', {}, async () => {
    if (!shell) throw Error('Not open');
    touch();
    if (!pending.length && !closed) await new Promise(resolve => {
      const timer = setTimeout(resolve, 150);
      wake = () => { clearTimeout(timer); resolve(); };
    });
    wake = undefined;
    const data = pending.subarray(0, 65536); pending = pending.subarray(data.length);
    if (pending.length < 128 * 1024 && !closed) shell.resume();
    return { data: data.toString('base64'), closed: closed && !pending.length, error: failure };
  });
  register('ssh_terminal_write', { data: z.string().max(12000) }, async ({ data }) => {
    if (!shell || closed) throw Error('Closed');
    const bytes = Buffer.from(data, 'base64');
    if (!bytes.length || bytes.length > 8192 || bytes.toString('base64') !== data) throw Error('Invalid input');
    touch(); shell.write(bytes); return { ok: true };
  });
  register('ssh_terminal_resize', { cols: z.number().int().min(2).max(400), rows: z.number().int().min(2).max(200) }, async ({ cols, rows }) => {
    if (!shell || closed) throw Error('Closed');
    shell.setWindow(rows, cols, 0, 0); return { ok: true };
  });
  register('ssh_terminal_close', {}, async () => {
    shell?.end(); shell?.destroy(); finish(); pending = Buffer.alloc(0); return { ok: true };
  });
}
