/* Dedicated local Vite only; all API requests mocked, no real SSH or database access. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
async function verify(page, origin = 'http://127.0.0.1:3108') {
  assert.ok(['127.0.0.1', 'localhost'].includes(new URL(origin).hostname));
  const checks = [], writes = [], requests = [], unexpected = [], errors = [];
  const password = 'fixture-only-GPU-password', jumpSecret = 'fixture-only-jump-password', output = path.resolve(__dirname, '../output/playwright');
  const safeFields = fields => ({ ...fields, ...(fields.jump ? { jump: { host: fields.jump.host, port: fields.jump.port ?? 22, username: fields.jump.username || 'root', has_password: true } } : {}) });
  fs.mkdirSync(output, { recursive: true });
  let role = 'admin', failSave = false, failDelete = false, failImport = false, failList = false;
  const makeServer = (id, fields = {}) => ({ id, name: '训练机 A', host: '192.0.2.10', port: 22, username: 'root', enabled: true, has_password: true, revision: 1, source: 'password', ...fields });
  let servers = [{ id: 'default', name: '默认连接', host: null, port: null, username: null, enabled: true, has_password: false, revision: 1, source: 'ssh_manager' }, makeServer('11111111-1111-4111-8111-111111111111')], nextId = 2;
  const snapshot = { server_name: '隔离GPU快照', collected_at: '2026-10-28T00:00:00Z', cached: false, processes_complete: true, warnings: [], gpus: [], processes: [] };
  const handler = async route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname, method = req.method();
    if (!p.startsWith('/api/')) return url.origin === origin ? route.continue() : route.abort();
    requests.push({ path: p, method, authorization: req.headers().authorization });
    const json = (data, status = 200) => route.fulfill({ status, json: data });
    if (p === '/api/auth/me') return json({ id: 1, tenant_id: 1, username: '服务器隔离夹具', role });
    if (method !== 'GET') writes.push({ path: p, method, body: req.postDataJSON() });
    if (p === '/api/gpu-resources/servers/test') return json(snapshot);
    if (p === '/api/gpu-resources/servers/import') {
      if (failImport) return json({ detail: '第 1 条连接重复，本批未导入。' }, 409);
      const rows = req.postDataJSON().servers.map(({ password: secret, ...fields }) => makeServer(`import-${nextId++}`, { ...safeFields(fields), has_password: !!secret }));
      servers.push(...rows); return json({ imported: rows.length, items: rows }, 201);
    }
    if (p === '/api/gpu-resources/servers') {
      if (method === 'GET') return failList ? json({ detail: '夹具列表失败' }, 503) : json(servers);
      if (method === 'POST') {
        if (failSave) return json({ detail: '夹具保存失败' }, 503);
        const { password: secret, ...fields } = req.postDataJSON(), saved = makeServer(`created-${nextId++}`, { ...safeFields(fields), has_password: !!secret });
        servers.push(saved); return json(saved, 201);
      }
    }
    const server = servers.find(row => p === `/api/gpu-resources/servers/${row.id}`);
    if (server && method === 'PATCH') {
      const { password: secret, clear_password, ...fields } = req.postDataJSON();
      Object.assign(server, safeFields(fields), { has_password: clear_password ? false : secret ? true : server.has_password, revision: server.revision + 1 }); return json(server);
    }
    if (server && method === 'DELETE') {
      if (failDelete) return json({ detail: '夹具删除失败' }, 503);
      servers = servers.filter(row => row.id !== server.id); return route.fulfill({ status: 204 });
    }
    if (p === '/api/gpu-resources' || p.endsWith('/resources')) return json(snapshot);
    if (p.endsWith('/release/tasks')) return json([]);
    if (p === '/api/projects') return json({ items: [], total: 0 });
    unexpected.push(`${method} ${p}`); return json({ detail: 'Unexpected fixture request' }, 500);
  };
  await page.context().route('**/*', handler);
  await page.addInitScript(() => {
    if (!['http:', 'https:'].includes(location.protocol)) return;
    localStorage.setItem('token', 'fixture-only');
    if (!localStorage.getItem('user')) localStorage.setItem('user', JSON.stringify({ id: 1, tenant_id: 1, role: 'admin' }));
  });
  page.on('pageerror', error => errors.push(error.message)); page.setDefaultTimeout(12000);
  const button = (name, scope = page) => scope.getByRole('button', { name: new RegExp(name + '$') });
  const row = name => page.getByRole('row').filter({ hasText: name });
  const form = () => page.getByRole('dialog', { name: /^(新增|编辑)GPU服务器$/ });
  const fill = async name => {
    await form().getByRole('textbox', { name: /连接名称$/ }).fill(name);
    await form().getByRole('textbox', { name: /服务器IP$/ }).fill('192.0.2.30');
    await form().getByLabel('密码', { exact: true }).fill(password);
  };
  const noSecret = async () => {
    assert.equal((await page.locator('body').innerText()).includes(password), false);
    assert.equal(await page.evaluate(secret => [...Object.values(localStorage), ...Object.values(sessionStorage)].some(value => value.includes(secret)), password), false);
    assert.equal((await page.locator('body').innerText()).includes(jumpSecret), false);
    assert.equal(await page.evaluate(secret => [...Object.values(localStorage), ...Object.values(sessionStorage)].some(value => value.includes(secret)), jumpSecret), false);
  };
  const jsonFile = async data => {
    const chooser = page.waitForEvent('filechooser'); await button('选择 JSON 文件').click();
    await (await chooser).setFiles({ name: 'servers.json', mimeType: 'application/json', buffer: Buffer.isBuffer(data) ? data : Buffer.from(typeof data === 'string' ? data : JSON.stringify(data)) });
  };
  let stage = 'list';
  try {
    await page.goto(`${origin}/gpu-servers`); await row('默认连接').waitFor();
    assert.equal(await row('默认连接').getByRole('button').count(), 0); assert.equal(await row('默认连接').getByRole('switch').count(), 0);
    assert.equal(await row('训练机 A').getByRole('switch').isChecked(), true);
    await row('训练机 A').getByRole('switch').click(); await page.waitForFunction(() => document.querySelector('.ant-switch-checked') === null);
    assert.deepEqual(writes.at(-1).body, { enabled: false });
    await row('训练机 A').getByRole('switch').click(); await row('训练机 A').locator('.ant-switch-checked').waitFor(); assert.deepEqual(writes.at(-1).body, { enabled: true });
    checks.push('ssh-manager connection read-only; custom connection can stop collection');
    stage='search-and-default-alias';servers[0].name='绍兴a100';servers.push(makeServer('default-alias',{name:'绍兴A100',host:'36.138.253.82'}));
    await page.reload();await row('绍兴A100').waitFor();assert.equal(await page.getByText('绍兴a100',{exact:true}).count(),0);
    const search=page.getByRole('searchbox',{name:'搜索服务器',exact:true}),beforeSearch=writes.length;
    await search.fill('A100');assert.equal(await row('训练机 A').count(),0);await row('绍兴A100').waitFor();
    await search.fill('36.138.253');await row('绍兴A100').waitFor();
    await search.fill('NO-SERVER-MATCH');await page.getByText('没有匹配的服务器',{exact:true}).waitFor();
    await search.fill('');await row('训练机 A').waitFor();assert.equal(writes.length,beforeSearch);
    checks.push('case-insensitive connection-name/IP search, empty result/reset and one editable default-server alias');
    stage = 'create-edit'; await button('新增服务器').click(); await fill('新增离线机');
    await button('测试连接', form()).click(); await form().getByText('未保存连接测试结果', { exact: true }).waitFor();
    await noSecret(); failSave = true; await button('保存连接', form()).click(); await form().getByText('夹具保存失败', { exact: true }).waitFor();
    failSave = false; await button('保存连接', form()).click(); await form().waitFor({ state: 'hidden' }); await row('新增离线机').waitFor();
    assert.equal(writes.find(req => req.path.endsWith('/servers') && req.method === 'POST').body.password, password);
    await button('编辑连接', row('新增离线机')).click(); assert.equal(await form().getByLabel('密码', { exact: true }).inputValue(), '');
    await form().getByRole('textbox', { name: /连接名称$/ }).fill('改名连接'); await button('保存连接', form()).click(); await form().waitFor({ state: 'hidden' });
    assert.equal('password' in writes.at(-1).body, false);
    await button('编辑连接', row('改名连接')).click(); await form().getByRole('textbox', { name: /服务器IP$/ }).fill('192.0.2.31');
    const before = writes.length; await button('保存连接', form()).click(); await form().getByText('更改IP、端口或用户名时必须重填密码', { exact: true }).waitFor(); assert.equal(writes.length, before);
    await form().getByLabel('密码', { exact: true }).fill(password); await button('保存连接', form()).click(); await form().waitFor({ state: 'hidden' });
    await noSecret(); checks.push('connection test, create error/retry, edit secret retention and target-change password validation');
    stage='jump';await button('新增服务器').click();await fill('跳板连接');
    await form().getByRole('switch',{name:'通过跳板机连接',exact:true}).click();
    await form().getByLabel('跳板机IP',{exact:true}).fill('192.0.2.1');
    await form().getByLabel('跳板机密码',{exact:true}).fill(jumpSecret);
    await button('测试连接',form()).click();await form().getByText('未保存连接测试结果',{exact:true}).waitFor();
    assert.equal(writes.at(-1).body.jump.password,jumpSecret);
    await button('保存连接',form()).click();await form().waitFor({state:'hidden'});await row('跳板连接').getByText('跳板机密码已设置',{exact:true}).waitFor();
    await button('编辑连接',row('跳板连接')).click();assert.equal(await form().getByLabel('跳板机密码',{exact:true}).inputValue(),'');
    await button('保存连接',form()).click();await form().waitFor({state:'hidden'});assert.equal('password' in writes.at(-1).body.jump,false);
    await button('编辑连接',row('跳板连接')).click();await form().getByLabel('跳板机IP',{exact:true}).fill('192.0.2.2');
    const jumpBefore=writes.length;await button('保存连接',form()).click();await form().getByText('新增或修改跳板机时必须填写跳板机密码',{exact:true}).waitFor();assert.equal(writes.length,jumpBefore);
    await form().getByRole('switch',{name:'通过跳板机连接',exact:true}).click();await button('保存连接',form()).click();await form().waitFor({state:'hidden'});assert.equal(writes.at(-1).body.jump,null);
    await noSecret();checks.push('jump create/test, encrypted-summary display, blank secret retention, changed-address validation and remove jump');
    stage = 'json'; await button('JSON 批量导入').click(); let modal = page.getByRole('dialog', { name: 'JSON 批量导入服务器', exact: true });
    const download = page.waitForEvent('download'); await button('下载 JSON 模板', modal).click(); assert.equal((await download).suggestedFilename(), 'gpu-servers-template.json');
    const countBefore = writes.length; await jsonFile(`{"servers":[{"password":"${password}"`); await modal.getByText(/JSON 格式无效/).waitFor(); await noSecret();
    await jsonFile({ servers: Array.from({ length: 201 }, () => ({ name: '测试', host: '192.0.2.40', password })) }); await modal.getByText('servers 必须包含 1 至 200 条连接。', { exact: true }).waitFor();
    await jsonFile(Buffer.alloc(1024 ** 2 + 1, ' ')); await modal.getByText('JSON 文件不能超过 1 MiB。', { exact: true }).waitFor(); assert.equal(writes.length, countBefore);
    const importInput = { servers: [{ name: 'JSON导入机', host: '192.0.2.40', password, port: 22, username: 'root', enabled: false, jump: {host:'192.0.2.9',port:22,username:'root',password:jumpSecret} }] };
    await jsonFile(importInput); await modal.getByText('已读取 1 条连接，可提交导入。', { exact: true }).waitFor();
    failImport = true; await button('导入 1 条连接', modal).click(); await modal.getByText('第 1 条连接重复，本批未导入。', { exact: true }).waitFor(); await noSecret();
    failImport = false; await button('导入 1 条连接', modal).click(); await modal.waitFor({ state: 'hidden' }); await row('JSON导入机').waitFor();
    assert.deepEqual(writes.filter(req => req.path.endsWith('/import')).at(-1).body, importInput);
    checks.push('template; malformed/over-200/over-1MiB blocked; atomic JSON error/retry without secret echo');
    stage = 'delete'; await button('删除连接', row('改名连接')).click(); const deletion = page.getByRole('dialog', { name: '删除GPU服务器？', exact: true });
    assert.equal(writes.filter(req => req.method === 'DELETE').length, 0);
    failDelete = true; await button('确认删除', deletion).click(); await deletion.getByText('夹具删除失败', { exact: true }).waitFor();
    failDelete = false; await button('确认删除', deletion).click(); await deletion.waitFor({ state: 'hidden' }); assert.equal(await row('改名连接').count(), 0);
    checks.push('delete confirmation and failed delete retry');
    stage = 'resource-default-alias';
    for (const route of ['/gpu-resources','/gpu-resources?server_id=default']) {
      await page.goto(origin+route);
      await page.getByText('root@36.138.253.82 · 端口 22',{exact:true}).waitFor();
      assert.equal(await page.getByText('原ssh-manager连接 · 配置只读',{exact:true}).count(),0);
    }
    await page.getByRole('combobox',{name:'GPU服务器',exact:true}).click();
    assert.equal(await page.locator('.ant-select-dropdown:visible').getByText('绍兴a100',{exact:true}).count(),0);
    await page.locator('.ant-select-dropdown:visible').getByText('绍兴A100',{exact:true}).waitFor();
    await page.getByRole('combobox',{name:'GPU服务器',exact:true}).press('Escape');
    const alias=servers.find(server=>server.id==='default-alias');servers=servers.filter(server=>server!==alias);
    await page.goto(origin+'/gpu-resources?server_id=default');
    await page.getByText('原ssh-manager连接 · 配置只读',{exact:true}).waitFor();
    servers.push(alias);
    checks.push('GPU resources default and legacy links select the managed alias; duplicate readonly default hidden');
    await page.goto(origin+'/gpu-servers');await row('训练机 A').waitFor();
    stage = 'resources-navigation'; await row('训练机 A').getByRole('link', { name: '查看资源', exact: true }).click(); await page.getByRole('combobox', { name: 'GPU服务器', exact: true }).waitFor();
    assert.equal(await button('新增服务器').count(), 0); assert.equal(await button('编辑连接').count(), 0); assert.equal(await button('删除连接').count(), 0);
    const selector=page.getByRole('combobox',{name:'GPU服务器',exact:true});
    await selector.fill(' a100 ');
    await page.locator('.ant-select-dropdown:visible').getByText('绍兴A100',{exact:true}).waitFor();
    assert.equal(await page.locator('.ant-select-dropdown:visible').getByText('训练机 A',{exact:true}).count(),0);
    await selector.fill('36.138.253');
    await page.locator('.ant-select-dropdown:visible').getByText('绍兴A100',{exact:true}).waitFor();
    await selector.fill('不存在的服务器');
    await page.locator('.ant-select-dropdown:visible').getByText('无匹配服务器',{exact:true}).waitFor();
    await selector.fill('');
    await page.locator('.ant-select-dropdown:visible').getByText('训练机 A',{exact:true}).waitFor();
    await selector.fill('36.138.253');await selector.press('Enter');
    await page.getByText('root@36.138.253.82 · 端口 22',{exact:true}).waitFor();
    await selector.click();await selector.fill('训练机');await selector.press('Escape');
    await page.getByText('root@36.138.253.82 · 端口 22',{exact:true}).waitFor();
    checks.push('GPU resource selector fuzzy name/IP search, trim/case handling, empty/reset, selection and Escape without switching');
    await page.getByRole('link', { name: '服务器管理', exact: true }).click(); await row('JSON导入机').waitFor();
    checks.push('resource selector retained; resource page has no connection mutation controls and links to manager');
    stage = 'mobile'; await page.setViewportSize({ width: 390, height: 844 }); await page.waitForFunction(() => document.documentElement.scrollWidth <= innerWidth);
    await page.waitForFunction(() => document.querySelectorAll('.ant-message-notice').length === 0);
    await page.screenshot({ path: path.join(output, 'gpu-servers-mobile.png'), animations: 'disabled' }); await button('新增服务器').click(); await form().getByRole('textbox', { name: /连接名称$/ }).waitFor();
    await page.waitForFunction(() => document.documentElement.scrollWidth <= innerWidth);
    await page.waitForFunction(() => [...document.querySelectorAll('.ant-modal')].some(element => getComputedStyle(element).opacity === '1' && getComputedStyle(element).transform === 'none'));
    await page.screenshot({ path: path.join(output, 'gpu-servers-form-mobile.png'), animations: 'disabled' }); await button('取\\s*消', form()).click();
    await button('JSON 批量导入').click(); modal = page.getByRole('dialog', { name: 'JSON 批量导入服务器', exact: true }); assert.equal(await modal.getByText('已读取 1 条连接，可提交导入。', { exact: true }).count(), 0);
    await page.waitForFunction(() => document.documentElement.scrollWidth <= innerWidth);
    await page.waitForFunction(() => [...document.querySelectorAll('.ant-modal')].some(element => getComputedStyle(element).opacity === '1' && getComputedStyle(element).transform === 'none'));
    await page.screenshot({ path: path.join(output, 'gpu-servers-import-mobile.png'), animations: 'disabled' }); await button('取\\s*消', modal).click();
    checks.push('390px list/form/JSON fit viewport; closing clears pending credentials');
    stage = 'list-failure'; failList = true; await page.reload(); await page.getByText('夹具列表失败', { exact: true }).first().waitFor(); failList = false; await button('重试列表').click(); await row('JSON导入机').waitFor();
    role = 'annotator'; await page.evaluate(() => localStorage.setItem('user', JSON.stringify({ role: 'annotator' })));
    const reads = requests.filter(req => req.path.includes('/gpu-resources')).length; await page.goto(`${origin}/gpu-servers`); await page.waitForURL('**/annotations');
    assert.equal(requests.filter(req => req.path.includes('/gpu-resources')).length, reads); await noSecret(); assert.deepEqual(unexpected, []); assert.deepEqual(errors, []);
    assert.ok(requests.every(req => req.authorization === 'Bearer fixture-only')); checks.push('list retry; non-admin cannot read server data; all API authenticated');
    return { checks, mockedRequests: requests.length, mockedWrites: writes.length, realBusinessWrites: 0, unexpected, runtimeErrors: errors };
  } catch (error) {
    await page.screenshot({ path: path.join(output, 'gpu-servers-failure.png'), fullPage: true }); fs.writeFileSync(path.join(output, 'gpu-servers-failure-aria.txt'), await page.locator('body').ariaSnapshot());
    throw Error(`${stage}: ${error.message}; unexpected=${JSON.stringify(unexpected)}; runtime=${JSON.stringify(errors)}`);
  } finally { await page.goto('about:blank'); await page.context().unroute('**/*', handler); }
}
module.exports = verify;
if (require.main === module) {
  const pw = require(path.join(process.env.USERPROFILE, '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));
  (async () => {
    const browser = await pw.chromium.launch({ channel: 'msedge', headless: true });
    try { const context = await browser.newContext({ serviceWorkers: 'block', acceptDownloads: true }); const result = await verify(await context.newPage(), process.argv[2]); fs.writeFileSync(path.resolve(__dirname, '../output/playwright/gpu-servers-verification.json'), JSON.stringify(result, null, 2)); console.log(JSON.stringify(result, null, 2)); }
    finally { await browser.close(); }
  })().catch(error => { console.error(error); process.exitCode = 1; });
}
