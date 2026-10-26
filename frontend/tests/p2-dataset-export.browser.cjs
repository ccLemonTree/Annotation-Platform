/* Run against Vite; every API request is intercepted before it can reach a backend. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

(async () => {
  const base = new URL(process.env.P2_FRONTEND_URL || 'http://127.0.0.1:3184');
  assert(['127.0.0.1', 'localhost'].includes(base.hostname), 'Use an isolated local frontend');
  const browser = await chromium.launch({ headless: true,
    ...(process.env.P2_BROWSER_EXECUTABLE ? { executablePath: process.env.P2_BROWSER_EXECUTABLE } : { channel: 'msedge' }) });
  const context = await browser.newContext({ serviceWorkers: 'block', viewport: { width: 1440, height: 1200 } });
  const page = await context.newPage();
  const unexpected = [], errors = [], requests = [];
  page.on('pageerror', error => errors.push(error.message));
  const projectId = 'p2-dataset-synthetic';
  const root = `/api/export/projects/${projectId}`;
  const stamp = '2026-10-15T00:00:00Z';
  const assets = ['z', 'a', 'm'].map(id => ({ id, project_id: projectId, original_name: `${id}.png`,
    source_type: 'upload', annotation_status: 'reviewed', width: 64, height: 48, format: 'PNG',
    created_at: stamp, updated_at: stamp }));
  const annotations = assets.map((asset, index) => ({ id: `ann-${asset.id}`, asset_id: asset.id, is_latest: true,
    version: 1, review_status: 'approved', annotation_data: { boxes: index === 2 ? [] : [
      { x: 1, y: 1, width: 10, height: 10, label: '目标' },
      ...(index === 0 ? [{ x: 20, y: 1, width: 10, height: 10, label: '次类' }] : [])] } }));
  const sources = Object.fromEntries(assets.map(asset => [asset.id, { group_id: `group-${asset.id}` }]));
  const zip = Buffer.from('504b0506000000000000000000000000000000000000', 'hex');
  const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jWAAAAABJRU5ErkJggg==', 'base64');
  const snapshots = [{ id: 'legacy', format: 'coco', created_at: stamp,
    manifest: { assets: [{}], labels: ['历史'], distribution: {} } }];
  let empty = false;
  await context.addInitScript(() => localStorage.setItem('token', 'synthetic-ui-only'));
  await context.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url()), pathname = url.pathname;
    if (!pathname.startsWith('/api/')) {
      if (url.origin === base.origin && request.method() === 'GET') return route.continue();
      unexpected.push(`${request.method()} ${request.url()}`);
      return route.abort();
    }
    const method = request.method(), body = request.postDataJSON();
    requests.push({ method, path: pathname, body });
    const json = (data, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) });
    if (method === 'GET' && pathname === `/api/projects/${projectId}`)
      return json({ id: projectId, project_name: 'P2-04 浏览器合成验收', task_type: 'detection', status: 'active',
        label_schema: null, total_assets: 3, annotated_count: empty ? 0 : 3, reviewed_count: empty ? 0 : 3 });
    if (method === 'GET' && pathname === '/api/assets') return json({ items: assets, total: assets.length });
    if (method === 'GET' && pathname === '/api/annotations') return json({ items: empty ? [] : annotations, total: empty ? 0 : annotations.length });
    if (method === 'GET' && /^\/api\/assets\/[^/]+\/content$/.test(pathname))
      return route.fulfill({ contentType: 'image/png', body: png });
    if (method === 'GET' && pathname === `/api/stats/projects/${projectId}/distribution`) return json({});
    if (method === 'GET' && pathname === root + '/split-sources') return json(sources);
    if (method === 'GET' && pathname === root + '/snapshots') return json(snapshots);
    if (method === 'POST' && pathname === root + '/splits/preview') {
      // Fixture decisions only; the real grouping/threshold rules are exercised by Python/PG tests.
      const splits = body.suggest ? { a: 'val', m: 'test', z: 'train' } : body.splits;
      const ready = Object.values(splits).includes('val') && Object.values(splits).includes('test');
      return json({ splits, sources, groups: [['a'], ['m'], ['z']], policy: body.policy,
        missing_source_asset_ids: [], images_by_split: ready ? { train: 1, val: 1, test: 1 } : { train: 3 },
        images_by_label_by_split: { train: { '目标': 1, '次类': 1 }, val: { '目标': 1, '次类': 0 }, test: { '目标': 0, '次类': 0 } },
        warnings: ['合成拦截数据；不代表真实测试集已验收'], errors: ready ? [] : [{ code: 'missing_split', message: '缺少独立 val/test' }], can_freeze: ready });
    }
    if (method === 'PATCH' && pathname.startsWith(root + '/assets/') && pathname.endsWith('/split-source')) {
      const id = pathname.split('/').at(-2);
      sources[id] = Object.fromEntries(Object.entries(body).filter(([, value]) => value !== null));
      return json({ asset_id: id, source: sources[id] });
    }
    if (method === 'POST' && pathname === root + '/snapshots') {
      snapshots.unshift({ id: 'new', format: body.format, created_at: stamp,
        manifest: { assets: assets.map(asset => ({ asset_id: asset.id })), labels: body.labels, distribution: {},
          split_evidence: { policy: body.policy, sources: structuredClone(sources), splits: body.splits } } });
      return json(snapshots[0], 201);
    }
    if (method === 'GET' && /^\/api\/export\/snapshots\/(legacy|new)\/download$/.test(pathname))
      return route.fulfill({ contentType: 'application/zip', body: zip });
    unexpected.push(`${method} ${pathname}`);
    return route.abort();
  });
  const checkDisabled = async (locator, expected) => {
    await page.waitForFunction(({ label, value }) => [...document.querySelectorAll('button')]
      .some(button => button.textContent === label && button.disabled === value), { label: '冻结并下载', value: expected });
    assert.equal(await locator.isDisabled(), expected);
  };
  const downloadBytes = async button => {
    const promise = page.waitForEvent('download');
    await button.click();
    const stream = await (await promise).createReadStream();
    const chunks = [];
    for await (const chunk of stream) chunks.push(chunk);
    return Buffer.concat(chunks);
  };
  const output = path.resolve(process.env.P2_BROWSER_ARTIFACT_DIR || path.join(__dirname, '../../docs/testing/p2-04-browser'));
  try {
    await page.goto(new URL(`/projects/${projectId}/images`, base).href);
    await page.getByRole('button', { name: '导出 / 冻结数据集', exact: true }).click();
    const modal = page.getByRole('dialog', { name: '检测数据导出', exact: true });
    const freeze = modal.getByRole('button', { name: '冻结并下载', exact: true });
    await modal.getByRole('button', { name: '预览当前划分', exact: true }).waitFor({ state: 'visible' });
    await checkDisabled(freeze, true);
    await modal.getByRole('button', { name: '预览当前划分', exact: true }).click();
    await modal.getByText('缺少独立 val/test', { exact: true }).waitFor();
    await checkDisabled(freeze, true);
    await modal.getByRole('button', { name: '按来源分组自动建议', exact: true }).click();
    await checkDisabled(freeze, false);
    assert.deepEqual(requests.find(item => item.body?.suggest)?.body.labels, ['目标', '次类']);
    assert.equal(requests.find(item => item.body?.suggest)?.body.policy.training_budget_per_class, 100);
    assert.equal(requests.find(item => item.body?.suggest)?.body.policy.require_source, true);
    await modal.getByRole('spinbutton', { name: '每类训练图片预算', exact: true }).fill('2');
    await modal.getByRole('spinbutton', { name: 'pHash距离阈值', exact: true }).fill('1');
    await modal.getByRole('spinbutton', { name: '平均颜色阈值', exact: true }).fill('8');
    await checkDisabled(freeze, true);
    await modal.getByRole('button', { name: '预览当前划分', exact: true }).click();
    await checkDisabled(freeze, false);
    const oldDownload = modal.getByRole('button', { name: /下载 coco.*历史/ });
    assert.deepEqual(await downloadBytes(oldDownload), zip);
    await modal.getByRole('button', { name: '编辑来源', exact: true }).first().click();
    const editor = page.getByRole('dialog', { name: /编辑明确来源/ });
    await editor.getByRole('textbox', { name: /来源父图 ID/ }).fill('verified-parent');
    await editor.getByRole('button', { name: /确.*定/ }).click();
    await editor.waitFor({ state: 'hidden' });
    await checkDisabled(freeze, true);
    await modal.getByRole('button', { name: '预览当前划分', exact: true }).click();
    await checkDisabled(freeze, false);
    assert.deepEqual(await downloadBytes(freeze), zip);
    const frozen = requests.find(item => item.method === 'POST' && item.path === root + '/snapshots').body;
    assert.deepEqual(frozen.splits, { z: 'train', a: 'val', m: 'test' });
    assert.deepEqual(frozen.labels, ['目标', '次类']);
    assert.deepEqual(frozen.policy, { training_budget_per_class: 2, phash_distance: 1, color_distance: 8,
      require_source: true, require_independent: true });
    assert.deepEqual(await downloadBytes(oldDownload), zip);
    fs.mkdirSync(output, { recursive: true });
    await page.screenshot({ path: path.join(output, 'project-export.png'), fullPage: true });
    empty = true;
    await page.reload();
    await page.getByRole('button', { name: '导出 / 冻结数据集', exact: true }).click();
    await page.getByText('尚无已保存标注；“人”仅为界面默认候选，不代表项目已配置该类别。', { exact: true }).waitFor();
    await checkDisabled(page.getByRole('button', { name: '冻结并下载', exact: true }), true);
    assert.deepEqual(unexpected, [], 'Unexpected API/external request was blocked');
    assert.deepEqual(errors, [], 'Browser runtime errors');
    const result = { passed: true, fixture_only: true, backend_requests_forwarded: 0,
      checks: ['project entry', 'manual preview blocks invalid freeze', 'suggestion enables freeze despite ID order',
        'budget/threshold changes invalidate preview', 'source edit invalidates preview', 'freeze policy and splits payload',
        'legacy downloads before/after edit', 'no annotation fallback warning'],
      intercepted_api_requests: requests.length, unexpected, browser_errors: errors };
    fs.writeFileSync(path.join(output, 'result.json'), JSON.stringify(result, null, 2));
    console.log(JSON.stringify(result, null, 2));
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
