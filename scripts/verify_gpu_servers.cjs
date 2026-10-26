// Isolated Vite page, synthetic credentials, all API traffic intercepted; no real login or writes.
const assert = require('node:assert/strict');
const path = require('node:path');

async function verify(page, origin = 'http://localhost:3000') {
  page.setDefaultTimeout(7000);
  const errors = [], unexpected = [], writes = [], reads = [], checks = [];
  const password = 'fixture-only-GPU-password';
  const a = '11111111-1111-4111-8111-111111111111', b = '22222222-2222-4222-8222-222222222222';
  let servers = [
    { id: 'default', name: '默认连接', host: null, port: null, username: null, enabled: true, has_password: false, revision: 1, source: 'ssh_manager' },
    { id: a, name: '训练机 A', host: '192.0.2.10', port: 22, username: 'root', enabled: true, has_password: true, revision: 1, source: 'password' },
    { id: b, name: '训练机 B', host: '192.0.2.20', port: 22, username: 'root', enabled: false, has_password: false, revision: 1, source: 'password' },
  ];
  let failTest = false, failSave = false, failDelete = false, forbid = '', holdA = false, releaseA;
  const snapshot = name => ({ server_name: name, collected_at: '2026-10-16T00:00:00Z', cached: false,
    processes_complete: true, warnings: [],
    gpus: [{ index: 0, uuid: 'GPU-fixture-full-uuid-00000000000000000000', name: 'Fixture NVIDIA', driver_version: '550',
      memory_total_mib: 16000, memory_used_mib: 1000, memory_free_mib: 15000, utilization_percent: 0, temperature_c: 42,
      power_draw_w: 20, power_limit_w: 70, mig_mode: 'Disabled' }],
    processes: [{ gpu_uuid: 'GPU-fixture-full-uuid-00000000000000000000', pid: 100, name: 'fixture-compute', memory_used_mib: 1000 }] });
  await page.addInitScript(() => { if (!localStorage.getItem('user')) localStorage.setItem('user', JSON.stringify({ role: 'admin' })); });
  page.on('pageerror', e => errors.push(e.message));
  await page.route('**/api/**', async route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname, method = req.method();
    if (!p.startsWith('/api/')) return route.continue();
    const reply = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (method === 'GET') reads.push(p); else writes.push({ path: p, method, body: req.postDataJSON() });
    if (forbid && (forbid === 'list' ? p.endsWith('/servers') : p.endsWith('/resources'))) return reply({ detail: '无权限' }, 403);
    if (p === '/api/gpu-resources/servers/test' && method === 'POST') {
      return failTest ? reply({ detail: 'fixture 连接失败' }, 503) : reply(snapshot('未保存测试目标'));
    }
    if (p === '/api/gpu-resources/servers') {
      if (method === 'GET') return reply(servers);
      if (method === 'POST') {
        if (failSave) return reply({ detail: 'fixture 保存失败' }, 503);
        const { password: secret, ...fields } = req.postDataJSON();
        const saved = { ...fields, id: '33333333-3333-4333-8333-333333333333', has_password: !!secret, source: 'password', revision: 1 };
        servers.push(saved); return reply(saved, 201);
      }
    }
    if (p === '/api/gpu-resources') return reply(snapshot('默认快照'));
    const match = p.match(/^\/api\/gpu-resources\/servers\/([^/]+)(\/resources)?$/);
    if (match) {
      const server = servers.find(s => s.id === match[1]);
      if (match[2] && method === 'GET') {
        if (server.id === a && holdA) await new Promise(resolve => { releaseA = resolve; });
        return reply(snapshot(server.id === a ? 'A 的快照' : `${server.name} 的快照`));
      }
      if (method === 'PATCH') {
        const { password: secret, clear_password, ...fields } = req.postDataJSON();
        Object.assign(server, fields, { has_password: clear_password ? false : secret ? true : server.has_password, revision: server.revision + 1 });
        return reply(server);
      }
      if (method === 'DELETE') {
        if (failDelete) return reply({ detail: 'fixture 删除失败' }, 503);
        servers = servers.filter(s => s.id !== server.id);
        return route.fulfill({ status: 204, body: '' });
      }
    }
    unexpected.push(`${method} ${p}`); return reply({ detail: 'unexpected fixture request' }, 500);
  });
  const source = await (await page.request.get(origin + '/src/components/AssetImage.tsx')).text();
  const react = source.match(/["']([^"']*\/react\.js\?[^"']*)["']/)?.[1];
  assert.ok(react, 'Vite React runtime available');
  await page.route('**/src/App.tsx*', route => route.fulfill({ contentType: 'application/javascript', body: `
    import React from '${react}'; import Provider from '/src/ui/PlatformProvider.tsx';
    import GpuResourcesPage from '/src/pages/GpuResourcesPage.tsx';
    export default function App(){return React.createElement(Provider,null,React.createElement('main',{style:{padding:16,minWidth:0}},React.createElement(GpuResourcesPage)));}` }));
  const button = name => page.getByRole('button', { name, exact: true });
  const choose = async name => {
    await page.getByRole('combobox', { name: 'GPU服务器', exact: true }).click();
    await page.locator('.ant-select-dropdown:visible .ant-select-item-option-content').getByText(name, { exact: true }).click();
  };
  const dialog = () => page.getByRole('dialog', { name: /^(新增|编辑)GPU服务器$/ });
  const fill = async (name = '新增离线机') => {
    await dialog().getByLabel('连接名称', { exact: true }).fill(name);
    await dialog().getByLabel('服务器IP', { exact: true }).fill('192.0.2.30');
    await dialog().getByLabel('密码', { exact: true }).fill(password);
  };
  const save = async () => {
    await dialog().getByRole('button', { name: '保存连接', exact: true }).click();
    assert.equal(await page.getByRole('dialog', { name: '确认连接参数' }).count(), 0, 'save submits directly without second approval');
  };
  const noSecret = async () => {
    assert.equal(await page.evaluate(secret => [...Object.values(localStorage), ...Object.values(sessionStorage)].some(v => v.includes(secret)), password), false, 'credentials never persist in browser storage');
    assert.ok(!(await page.locator('body').innerText()).includes(password), 'credentials never echoed in content');
  };
  try {
    await page.goto(origin + '/gpu-resources');
    // RED before implementation: this control does not exist on the original page.
    await button('新增服务器').waitFor();
    await page.getByText('默认快照', { exact: true }).waitFor();
    await page.clock.install();
    await page.getByText('原ssh-manager连接', { exact: false }).first().waitFor();
    assert.equal(await button('编辑连接').count(), 0); assert.equal(await button('删除连接').count(), 0);
    checks.push('legacy default read-only connection uses original endpoint');

    holdA = true; await choose('训练机 A');
    await page.waitForFunction(() => !document.body.textContent.includes('默认快照'));
    await assertEventually(() => releaseA, 'A request held');
    await choose('训练机 B');
    await page.getByText('此连接已停用，启用后自动采集。', { exact: true }).waitFor();
    await page.clock.runFor(60001);
    holdA = false; releaseA();
    await page.waitForTimeout(150);
    assert.equal(await page.getByText('A 的快照', { exact: true }).count(), 0);
    assert.ok(!reads.includes(`/api/gpu-resources/servers/${b}/resources`), 'disabled target never collected');
    await page.getByRole('switch', { name: '启用采集', exact: true }).click();
    await page.getByText('此连接未设置密码，请填写密码后采集。', { exact: true }).waitFor();
    await page.clock.runFor(60001);
    assert.ok(!reads.includes(`/api/gpu-resources/servers/${b}/resources`), 'enabled target without password never collected');
    await page.getByText('未设置密码', { exact: true }).waitFor();
    await button('填写密码').click();
    await dialog().getByLabel('密码', { exact: true }).fill(password); await save();
    await dialog().waitFor({ state: 'hidden' });
    await page.getByText('训练机 B 的快照', { exact: true }).waitFor();
    checks.push('cross-server abort/late response isolation; disabled/missing-password do not poll; password action resumes collection');

    await button('新增服务器').click(); await fill();
    const secret = dialog().getByLabel('密码', { exact: true });
    assert.equal(await secret.getAttribute('autocomplete'), 'new-password');
    assert.equal(await secret.getAttribute('maxlength'), '1024');
    assert.equal(await dialog().getByLabel('用户名', { exact: true }).getAttribute('maxlength'), '64');
    await dialog().getByLabel('用户名', { exact: true }).fill('root$'); await save();
    await dialog().getByText('请输入有效的Linux用户名', { exact: false }).waitFor();
    await dialog().getByLabel('用户名', { exact: true }).fill('root');
    const before = writes.length;
    failTest = true; await dialog().getByRole('button', { name: '测试连接', exact: true }).click();
    await dialog().getByText('fixture 连接失败', { exact: false }).waitFor();
    assert.equal(writes.length, before + 1); assert.ok(writes.at(-1).path.endsWith('/test'));
    assert.equal(servers.length, 3, 'test never saves a server');
    assert.equal(await dialog().getByText('未保存连接测试结果', { exact: true }).count(), 0);
    failTest = false; await dialog().getByRole('button', { name: '测试连接', exact: true }).click();
    await dialog().getByText('未保存连接测试结果', { exact: true }).waitFor();
    await page.getByText('训练机 B 的快照', { exact: true }).waitFor();
    await noSecret();
    await dialog().getByRole('button', { name: /^取\s*消$/ }).click();
    await button('新增服务器').click(); assert.equal(await dialog().getByLabel('密码', { exact: true }).inputValue(), '');
    await fill('未测试也可保存');
    failSave = true; await save();
    await dialog().getByText('fixture 保存失败', { exact: false }).waitFor();
    failSave = false;
    await save();
    await dialog().waitFor({ state: 'hidden' });
    await page.getByText('未测试也可保存 的快照', { exact: true }).waitFor();
    const created = writes.find(w => w.method === 'POST' && w.path.endsWith('/servers') && w.body.name === '未测试也可保存');
    assert.deepEqual(created.body, { name: '未测试也可保存', host: '192.0.2.30', port: 22, username: 'root', password, enabled: true });
    checks.push('transient test failure/success isolated; cancel clears password; direct offline save/errors and auto-selection; credential input limits');

    await button('编辑连接').click();
    assert.equal(await dialog().getByLabel('密码', { exact: true }).inputValue(), '');
    await dialog().getByText('重填密码可测试未保存连接', { exact: false }).waitFor();
    assert.equal(await dialog().getByRole('button', { name: '测试连接', exact: true }).isDisabled(), true);
    await dialog().getByLabel('连接名称', { exact: true }).fill('改名机'); await save();
    await dialog().waitFor({ state: 'hidden' });
    assert.equal('password' in writes.at(-1).body, false, 'blank edit retains existing password');
    await page.getByText('改名机 的快照', { exact: true }).waitFor();
    await button('编辑连接').click();
    await dialog().getByLabel('服务器IP', { exact: true }).fill('192.0.2.31');
    await dialog().getByRole('button', { name: '保存连接', exact: true }).click();
    await dialog().getByText('更改IP、端口或用户名时必须重填密码', { exact: false }).waitFor();
    assert.equal(await page.getByRole('dialog', { name: '确认连接参数' }).count(), 0);
    await dialog().getByLabel('密码', { exact: true }).fill(password); await save();
    await dialog().waitFor({ state: 'hidden' }); assert.equal(writes.at(-1).body.password, password);
    await page.getByText('改名机 的快照', { exact: true }).waitFor();
    await button('编辑连接').click();
    await dialog().getByRole('checkbox', { name: '清除已保存密码', exact: true }).check(); await save();
    await dialog().waitFor({ state: 'hidden' });
    assert.equal(writes.at(-1).body.clear_password, true);
    assert.equal('password' in writes.at(-1).body, false);
    await page.getByText('未设置密码', { exact: true }).waitFor(); await noSecret();
    await page.getByText('此连接未设置密码，请填写密码后采集。', { exact: true }).waitFor();
    assert.equal(await page.getByText('改名机 的快照', { exact: true }).count(), 0);
    const afterClear = reads.length; await page.clock.runFor(60001);
    assert.equal(reads.length, afterClear, 'cleared-password row never polls invalid credentials');
    checks.push('edit never echoes secret; retain/replace/clear and target-change password validation; revision remount');

    await page.setViewportSize({ width: 390, height: 844 });
    await page.clock.runFor(4000);
    await page.waitForFunction(() => document.documentElement.scrollWidth <= innerWidth);
    await page.screenshot({ path: path.join(require('node:os').tmpdir(), 'gpu-servers-narrow.png'), fullPage: true, animations: 'disabled' });
    await button('编辑连接').click();
    await dialog().getByLabel('连接名称', { exact: true }).waitFor();
    await page.clock.runFor(500);
    await page.waitForFunction(() => document.documentElement.scrollWidth <= innerWidth);
    await page.screenshot({ path: path.join(require('node:os').tmpdir(), 'gpu-servers-form-narrow.png'), fullPage: true, animations: 'disabled' });
    await dialog().getByRole('button', { name: /^取\s*消$/ }).click();
    await page.setViewportSize({ width: 1280, height: 900 });
    await button('删除连接').click();
    const deletion = page.getByRole('dialog', { name: '删除GPU服务器？' });
    const deletes = writes.filter(w => w.method === 'DELETE').length;
    assert.equal(deletes, 0, 'delete requires confirmation');
    failDelete = true; await deletion.getByRole('button', { name: '确认删除', exact: true }).click();
    await deletion.getByText('fixture 删除失败', { exact: false }).waitFor();
    failDelete = false; await deletion.getByRole('button', { name: '确认删除', exact: true }).click();
    await deletion.waitFor({ state: 'hidden' }); await page.getByText('默认快照', { exact: true }).waitFor();
    checks.push('390px manager/modal fit; delete confirmation/error/retry and fallback to default');

    await choose('训练机 A'); await page.getByText('A 的快照', { exact: true }).waitFor();
    forbid = 'resources'; await button('手动刷新').click();
    await page.locator('#root').getByText('无权限', { exact: true }).waitFor();
    assert.equal(await button('新增服务器').count(), 0); assert.equal(await page.getByText('A 的快照', { exact: true }).count(), 0);
    forbid = 'list'; await page.reload(); await page.locator('#root').getByText('无权限', { exact: true }).waitFor();
    assert.equal(await page.getByRole('combobox', { name: 'GPU服务器', exact: true }).count(), 0);
    forbid = ''; await page.evaluate(() => localStorage.setItem('user', JSON.stringify({ role: 'user' })));
    const ordinaryReads = reads.length; await page.reload(); await page.locator('#root').getByText('无权限', { exact: true }).waitFor();
    assert.equal(reads.length, ordinaryReads, 'ordinary role never reads server list/resources');
    await noSecret(); assert.deepEqual(unexpected, []); assert.deepEqual(errors, []);
    checks.push('resource/list 403 erase manager and snapshot; non-admin makes no API requests; no secret persistence');
    return { checks, interceptedWrites: writes.length, realBusinessWrites: 0 };
  } catch (error) {
    console.error(JSON.stringify({ errors, unexpected, dialogs: await page.locator('[role=dialog]').evaluateAll(nodes => nodes.map(n => ({ title: n.querySelector('.ant-modal-title')?.textContent, hidden: n.getAttribute('aria-hidden'), labelled: n.getAttribute('aria-labelledby'), buttons: [...n.querySelectorAll('button')].map(b => ({ text: b.textContent, disabled: b.disabled, hidden: b.getAttribute('aria-hidden') })) }))), body: await page.locator('body').innerText() })); throw error;
  } finally { releaseA?.(); await page.goto('about:blank'); }
}
async function assertEventually(fn, message) {
  for (let i = 0; i < 100; i++) { if (fn()) return; await new Promise(resolve => setTimeout(resolve, 20)); }
  assert.fail(message);
}
module.exports = verify;
if (require.main === module) {
  let pw;
  try { pw = require('playwright'); } catch { pw = require(path.join(process.env.USERPROFILE, '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright')); }
  (async () => {
    const browser = await pw.chromium.launch({ channel: 'msedge', headless: true });
    try { const context = await browser.newContext({ serviceWorkers: 'block' }); console.log(JSON.stringify(await verify(await context.newPage(), process.argv[2]), null, 2)); }
    finally { await browser.close(); }
  })().catch(e => { console.error(e); process.exitCode = 1; });
}
