const assert = require('node:assert/strict');
const path = require('node:path');

async function verify(page, origin = 'http://127.0.0.1:5176') {
  page.setDefaultTimeout(10000);
  const errors = [], unexpected = [], writes = [], reads = [];
  let role = 'admin', failSets = false, rejectSet = false, failImage = false, failItems = false, failReport = false;
  let holdList = false, releaseList, heldCount = 0, listCalls = 0, holdDetail = false, detailHeld = false, releaseDetail;
  const config = { inference_confidence: .001, confidence: .25, match_iou: .5, nms_iou: .45, label_mapping: {}, labels: [] };
  const set = { id: 'set-1', name: '人工真值集', project_id: null, labels: ['人'], total: 2, source: { kind: 'project', human_reviewed: true, independent: false }, created_at: '2026-10-16T00:00:00Z' };
  const metrics = { tp: 0, fp: 0, fn: 0, precision: null, recall: null, f1: null, map50: null, map50_95: null,
    per_class: [{ label: '人', truth_count: 0, prediction_count: 0, tp: 0, fp: 0, fn: 0, precision: null, recall: null, f1: null, ap50: null, ap50_95: null }],
    images: 0, negative_images: 0, false_positive_negative_images: 0, confusion_matrix: { labels: ['人', '背景'], matrix: [[0, 0], [0, 0]] },
    coverage: { total: 2, succeeded: 0, failed: 1, pending: 1, canceled: 0, complete: false } };
  let runs = [{ id: 'run-1', set_id: set.id, name: '空指标任务', status: 'failed', total: 2, processed: 1, failed_count: 1,
    model: { id: 'model-1', revision: 3, name: '已训练检测服务' }, config, metrics, error: 'fixture 模型不可用', created_at: set.created_at, updated_at: set.created_at }];
  const item = { id: 'item-1', asset_id: 'asset-1', original_name: '人工核对.png', width: 640, height: 480, status: 'completed',
    truth: [{ x: 10, y: 20, width: 100, height: 80, label: '人' }],
    predictions: [{ x: 15, y: 25, width: 100, height: 80, label: '人', score: .01 }], evaluation: { tp: 0, fp: 0, fn: 1 }, error: null };
  const paged = (items, url, total = items.length) => ({ items, total, page: Number(url.searchParams.get('page') || 1), page_size: Number(url.searchParams.get('page_size') || 20) });
  const handler = async route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname, method = req.method();
    if (!p.startsWith('/api/')) return route.continue();
    reads.push(method + ' ' + p + url.search);
    if (method !== 'GET') writes.push({ path: p, body: req.postDataJSON() });
    if (p === '/api/auth/me') return route.fulfill({ json: { id: 1, role, username: 'fixture', tenant_id: 1 } });
    if (p === '/api/training/servers') return route.fulfill({json:[{id:'default',name:'验证服务器',enabled:true}]});
    if (p === '/api/training/runs') return route.fulfill({json:{items:[],total:0}});
    if (p === '/api/projects') {
      const first = url.searchParams.get('page') !== '2';
      return route.fulfill({ json: paged(first ? Array.from({ length: 100 }, (_, i) => ({ id: 'p' + i, project_name: '项目' + i, task_type: 'detection' })) : [{ id: 'p100', project_name: '末页检测项目', task_type: 'detection' }], url, 101) });
    }
    if (p === '/api/model-services') return route.fulfill({ json: [
      { id: 'model-1', name: '已训练检测服务', kind: 'detection', enabled: true, revision: 3 },
      { id: 'off', name: '停用服务', kind: 'detection', enabled: false }, { id: 'caption', name: '描述服务', kind: 'caption', enabled: true },
    ] });
    if (/\/api\/export\/projects\/[^/]+\/snapshots$/.test(p)) return route.fulfill({ json: [
      { id: 'frozen', format: 'yolo', created_at: set.created_at, manifest: { labels: ['人'], assets: [{ split: 'val' }, { split: 'test' }], split_evidence: {} } },
      { id: 'legacy', format: 'yolo', created_at: set.created_at, manifest: { labels: ['人'], assets: [{ split: 'all' }] } },
    ] });
    if (p === '/api/validation/sets') {
      if (method === 'POST') return rejectSet ? route.fulfill({ status: 400, json: { detail: '没有符合条件的真值，请先完成人工审核通过' } }) : route.fulfill({ json: { ...set, id: 'created-set' } });
      return failSets ? route.fulfill({ status: 503, json: { detail: 'fixture 集合服务失败' } }) : route.fulfill({ json: paged([set], url, 21) });
    }
    if (p === '/api/validation/runs') {
      if (method === 'POST') return route.fulfill({ json: { ...runs[0], id: 'created-run' } });
      listCalls++;
      const oldRuns = structuredClone(runs);
      if (holdList) { heldCount++; await new Promise(resolve => { releaseList = resolve; }); }
      return route.fulfill({ json: paged(oldRuns, url) }).catch(() => {});
    }
    if (/\/api\/validation\/runs\/[^/]+\/(retry|cancel)$/.test(p) && method === 'POST') {
      runs = runs.map(r => ({ ...r, status: p.endsWith('retry') ? 'running' : 'canceled' }));
      return route.fulfill({ json: runs[0] });
    }
    if (p.endsWith('/report')) return failReport ? route.fulfill({ status: 500, json: { detail: 'fixture report error' } }) : route.fulfill({ json: { fixture: true, metrics } });
    if (p.endsWith('/items/item-1/image')) {
      assert.match(req.headers().authorization || '', /^Bearer /);
      if (failImage) return route.fulfill({ status: 404, json: { detail: 'fixture image deleted' } });
      return route.fulfill({ contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" width="640" height="480"><rect width="640" height="480" fill="#eee"/></svg>' });
    }
    if (p.endsWith('/items')) return failItems ? route.fulfill({ status: 500, json: { detail: 'fixture items error' } }) : route.fulfill({ json: paged([item], url) });
    if (/\/api\/validation\/runs\/[^/]+$/.test(p)) {
      const result = structuredClone(p.endsWith('run-2') ? { ...runs[0], id: 'run-2', name: '零分任务', metrics: { ...metrics, precision: 0, map50: 0 } } : runs[0]);
      if (holdDetail && p.endsWith('run-1')) { detailHeld = true; await new Promise(resolve => { releaseDetail = resolve; }); }
      return route.fulfill({ json: result }).catch(() => {});
    }
    unexpected.push(method + ' ' + p);
    return route.fulfill({ status: 500, json: { detail: 'unhandled fixture request' } });
  };
  await page.addInitScript(() => {
    localStorage.setItem('token', 'validation-fixture');
    window.validationUrls = { created: [], revoked: [] };
    const create = URL.createObjectURL.bind(URL), revoke = URL.revokeObjectURL.bind(URL);
    URL.createObjectURL = blob => { const url = create(blob); window.validationUrls.created.push(url); return url; };
    URL.revokeObjectURL = url => { window.validationUrls.revoked.push(url); revoke(url); };
  });
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/api/**', handler);
  const main = await (await page.request.get(origin + '/src/main.tsx')).text();
  const dom = main.match(/["']([^"']*react-dom_client[^"']*)["']/)?.[1];
  assert.ok(dom, 'Vite React runtime available');
  // Mount only this page; the parent owns App.tsx and the actual route.
  const source = await (await page.request.get(origin + '/src/components/AssetImage.tsx')).text();
  const react = source.match(/["']([^"']*\/react\.js\?[^"']*)["']/)?.[1];
  await page.route('**/src/App.tsx*', route => route.fulfill({ contentType: 'application/javascript', body: `
    import React from '${react}'; import Provider from '/src/ui/PlatformProvider.tsx';
    import ValidationPage from '/src/pages/ValidationPage.tsx';
    export default function App(){return React.createElement(Provider,null,React.createElement('main',{style:{padding:16,minWidth:0}},React.createElement(ValidationPage)));}` }));
  const choose = async (name, text) => {
    const box = page.getByRole('combobox', { name, exact: true });
    await box.click();
    if (name === '验证项目') await box.fill(text);
    await page.locator('.ant-select-dropdown:visible .ant-select-item-option-content').getByText(text, { exact: true }).click();
  };
  try {
    await page.goto(origin + '/validation');
    await page.getByText('验证集列表', { exact: true }).waitFor();
    await page.getByRole('cell', { name: '人工真值集', exact: true }).waitFor();
    assert.ok(reads.some(r => r.includes('/api/projects?page=2')), 'all project pages loaded');
    await page.getByRole('button', { name: '查看结果', exact: true }).first().click();
    await page.getByText('部分结果', { exact: false }).first().waitFor();
    assert.equal(await page.getByTestId('metric-precision').innerText(), '—');
    assert.equal(await page.getByTestId('metric-map50').innerText(), '—');
    assert.equal(await page.getByTestId('metric-tp').innerText(), '0');
    await page.getByText('fixture 模型不可用', { exact: false }).first().waitFor();
    await page.getByText('空指标任务 · 已训练检测服务 · r3 · 失败', { exact: true }).waitFor();
    await page.getByRole('row').filter({ hasText: '人工核对.png' }).getByRole('cell', { name: '已完成', exact: true }).waitFor();
    await page.getByRole('button', { name: '查看叠框', exact: true }).click();
    const preview = page.getByRole('dialog', { name: '真值与预测' });
    const image = preview.getByRole('img', { name: '人工核对.png' });
    await image.waitFor();
    assert.match(await image.getAttribute('src'), /^blob:/);
    assert.equal(await preview.locator('svg rect').count(), 2);
    await preview.getByText('人工核对.png · 已完成', { exact: true }).waitFor();
    await preview.getByText('虚线：预测（含低于工作点分数）', { exact: true }).waitFor();
    await preview.getByText('人 0.010', { exact: true }).waitFor();
    await preview.getByRole('button', { name: '关闭预览', exact: true }).click();
    assert.equal(await page.evaluate(() => window.validationUrls.created.every(u => window.validationUrls.revoked.includes(u))), true, 'image URL revoked on close');
    failImage = true;
    await page.getByRole('button', { name: '查看叠框', exact: true }).click();
    await page.getByText('原图读取失败', { exact: false }).waitFor();
    assert.equal(await page.getByRole('dialog', { name: '真值与预测' }).locator('svg rect').count(), 0);
    await page.getByRole('button', { name: '关闭预览', exact: true }).click();
    failImage = false;
    await Promise.all([page.waitForResponse(r => r.url().includes('error_type=false_negative')), choose('错误类型', '漏检')]);
    await Promise.all([page.waitForResponse(r => r.url().endsWith('/report')), page.getByRole('button', { name: '下载 JSON 报告', exact: true }).click()]);
    assert.equal(await page.evaluate(() => window.validationUrls.created.every(u => window.validationUrls.revoked.includes(u))), true, 'report URL revoked');
    failReport = true;
    await page.getByRole('button', { name: '下载 JSON 报告', exact: true }).click();
    await page.getByText('报告下载失败', { exact: false }).waitFor();
    failReport = false;
    failItems = true;
    await page.getByRole('button', { name: '刷新逐图结果', exact: true }).click();
    await page.getByText('逐图结果读取失败', { exact: false }).waitFor();
    failItems = false;
    await page.getByRole('button', { name: '创建验证集', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: '创建验证集' });
    await dialog.getByLabel('验证集名称', { exact: true }).fill('新验证集');
    await choose('验证项目', '末页检测项目');
    await dialog.getByRole('button', { name: '保存验证集', exact: true }).click();
    await dialog.waitFor({ state: 'hidden' });
    assert.deepEqual(writes.find(w => w.path.endsWith('/sets')).body, { name: '新验证集', project_id: 'p100', split: 'val' });
    await page.getByRole('button', { name: '创建验证集', exact: true }).click();
    await dialog.getByLabel('验证集名称', { exact: true }).fill('冻结测试集');
    await choose('验证项目', '末页检测项目');
    await choose('真值来源', '冻结包 val/test 划分');
    await choose('冻结划分', 'test（测试）');
    await page.getByRole('combobox', { name: '冻结包', exact: true }).click();
    const legacy = page.locator('.ant-select-dropdown:visible .ant-select-item-option').filter({ hasText: 'legacy' });
    await legacy.waitFor();
    assert.match(await legacy.getAttribute('class'), /disabled/);
    await page.locator('.ant-select-dropdown:visible .ant-select-item-option').filter({ hasText: 'frozen' }).click();
    rejectSet = true;
    await dialog.getByRole('button', { name: '保存验证集', exact: true }).click();
    await dialog.getByText('验证集创建失败：没有符合条件的真值，请先完成人工审核通过', { exact: true }).waitFor();
    rejectSet = false;
    await dialog.getByRole('button', { name: '保存验证集', exact: true }).click();
    await dialog.waitFor({ state: 'hidden' });
    assert.deepEqual(writes.filter(w => w.path.endsWith('/sets')).at(-1).body, { name: '冻结测试集', project_id: 'p100', split: 'test', snapshot_id: 'frozen' });
    await page.getByRole('button', { name: '验证权重', exact: true }).first().click();
    const runDialog = page.getByRole('dialog', { name: '验证训练权重' });
    await choose('权重来源','服务器已有权重路径');
    await page.getByRole('textbox',{name:'服务器权重路径',exact:true}).fill('/models/best.pt');
    await runDialog.getByLabel('类别映射 JSON', { exact: true }).fill('[]');
    await runDialog.getByRole('button', { name: '开始验证', exact: true }).click();
    await runDialog.getByText('请输入字符串到字符串的 JSON 对象', { exact: true }).waitFor();
    assert.equal(writes.filter(w => w.path.endsWith('/runs')).length, 0);
    await runDialog.getByLabel('类别映射 JSON', { exact: true }).fill('{"person":"人"}');
    await runDialog.getByRole('button', { name: '开始验证', exact: true }).click();
    await runDialog.waitFor({ state: 'hidden' });
    assert.deepEqual(writes.find(w => w.path.endsWith('/runs')).body.config, { ...config, label_mapping: { person: '人' } });
    assert.equal(writes.find(w=>w.path.endsWith('/runs')).body.checkpoint.weight_path,'/models/best.pt');
    assert.equal(writes.find(w=>w.path.endsWith('/runs')).body.checkpoint.device,'cpu');
    assert.equal(writes.find(w=>w.path.endsWith('/runs')).body.model_id,undefined);
    // A slow list request must complete before another poll is scheduled.
    runs[0].status = 'running'; holdList = true;
    await page.getByRole('button', { name: '刷新任务', exact: true }).click();
    for (let i = 0; !heldCount && i < 100; i++) await new Promise(resolve => setTimeout(resolve, 20));
    assert.equal(heldCount, 1);
    const heldCalls = listCalls;
    await page.waitForTimeout(3400);
    assert.equal(listCalls, heldCalls, 'no overlapping poll during slow response');
    holdList = false; releaseList();
    await page.getByRole('button', { name: '取消任务', exact: true }).waitFor();
    const polled = page.waitForResponse(r => new URL(r.url()).pathname === '/api/validation/runs');
    await polled;
    assert.ok(listCalls > heldCalls, 'active run polls after request completes');
    const beforeCancel = writes.length;
    await page.getByRole('button', { name: '取消任务', exact: true }).click();
    const cancelDialog = page.getByRole('dialog', { name: '取消验证任务？' });
    assert.equal(writes.length, beforeCancel, 'cancel requires confirmation');
    await cancelDialog.getByRole('button', { name: '确认取消任务', exact: true }).click();
    await cancelDialog.waitFor({ state: 'hidden' });
    assert.ok(writes.at(-1).path.endsWith('/cancel'));
    const stopped = listCalls;
    await page.waitForTimeout(3400);
    assert.ok(listCalls <= stopped + 1, 'terminal run stops polling');
    await page.getByRole('button', { name: '重试失败项', exact: true }).click();
    assert.ok(writes.at(-1).path.endsWith('/retry'));
    runs[0].status = 'completed';
    runs.push({ ...runs[0], id: 'run-2', name: '零分任务' });
    await page.getByRole('button', { name: '刷新任务', exact: true }).click();
    const secondRow = page.getByRole('row').filter({ hasText: '零分任务' });
    await secondRow.getByRole('button', { name: '查看结果', exact: true }).click();
    await page.getByTestId('metric-precision').filter({ hasText: '0.00%' }).waitFor();
    assert.equal(await page.getByTestId('metric-map50').innerText(), '0.00%');
    holdDetail = true;
    await page.getByRole('row').filter({ hasText: '空指标任务' }).getByRole('button', { name: '查看结果', exact: true }).click();
    for (let i = 0; !detailHeld && i < 100; i++) await new Promise(resolve => setTimeout(resolve, 20));
    assert.equal(detailHeld, true);
    await secondRow.getByRole('button', { name: '查看结果', exact: true }).click();
    await page.getByTestId('metric-precision').filter({ hasText: '0.00%' }).waitFor();
    holdDetail = false; releaseDetail();
    await page.waitForTimeout(200);
    assert.equal(await page.getByTestId('metric-precision').innerText(), '0.00%', 'stale run response cannot overwrite newly selected run');
    runs[0].metrics = { coverage: metrics.coverage };
    await page.getByRole('row').filter({ hasText: '空指标任务' }).getByRole('button', { name: '查看结果', exact: true }).click();
    await page.getByText('尚无可用指标', { exact: true }).waitFor();
    assert.equal(await page.getByTestId('metric-tp').innerText(), '—');
    await page.setViewportSize({ width: 390, height: 844 });
    await page.waitForFunction(() => document.documentElement.scrollWidth <= innerWidth);
    const narrow = await page.evaluate(() => ({ fits: document.documentElement.scrollWidth <= innerWidth, width: document.documentElement.scrollWidth,
      outside: [...document.querySelectorAll('main, .ant-space, .ant-space-item, .ant-card, .ant-descriptions')].map(el => ({ tag: el.className, width: el.getBoundingClientRect().width, x: el.getBoundingClientRect().x })).filter(el => el.x + el.width > innerWidth + 1) }));
    assert.equal(narrow.fits, true, '390px report and tables fit viewport: ' + JSON.stringify(narrow));
    await page.setViewportSize({ width: 1280, height: 900 });
    failSets = true;
    await page.getByRole('button', { name: '刷新验证集', exact: true }).click();
    await page.getByText('验证集读取失败', { exact: false }).waitFor();
    failSets = false;
    role = 'annotator';
    await page.reload();
    await page.getByText('只读模式', { exact: false }).waitFor();
    assert.equal(await page.getByRole('button', { name: '创建验证集', exact: true }).count(), 0);
    await page.setViewportSize({ width: 390, height: 844 });
    await page.waitForFunction(() => document.documentElement.scrollWidth <= innerWidth);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, '390px page fits viewport');
    assert.deepEqual(unexpected, []);
    assert.deepEqual(errors, []);
    return { checks: ['paged projects', 'null vs zero and coverage-only metrics', 'incomplete coverage/errors', 'authenticated image/overlay and URL cleanup', 'item filter/report and read failures', 'current/frozen creation and manual truth errors', 'creation parameters and invalid mapping', 'slow request polling/cancel confirmation/retry', 'stale detail response isolation', 'read-only/390px report tables'], interceptedWrites: writes.length, realBusinessWrites: 0 };
  } catch (error) {
    console.error(JSON.stringify({ errors, unexpected, buttons: await page.getByRole('button').evaluateAll(nodes => nodes.filter(n => /刷新|下载/.test(n.textContent)).map(n => n.outerHTML)), body: await page.locator('body').innerText() }));
    throw error;
  } finally {
    await page.goto('about:blank');
    await page.unroute('**/api/**', handler);
  }
}
module.exports = verify;
if (require.main === module) {
  let pw;
  try { pw = require('playwright'); } catch { pw = require(path.join(process.env.USERPROFILE, '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright')); }
  (async () => {
    const browser = await pw.chromium.launch({ channel: 'msedge', headless: true });
    try { const context = await browser.newContext({ serviceWorkers: 'block', acceptDownloads: true }); console.log(JSON.stringify(await verify(await context.newPage(), process.argv[2]), null, 2)); }
    finally { await browser.close(); }
  })().catch(e => { console.error(e); process.exitCode = 1; });
}
