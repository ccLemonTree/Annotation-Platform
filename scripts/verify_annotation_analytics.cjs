/* All API reads are fixtures and every write is intercepted before reaching a backend. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

async function verify(page, origin = 'http://127.0.0.1:5178') {
  const checks = [], heartbeats = [], unexpectedWrites = [], queries = [], errors = [];
  page.setDefaultTimeout(12000);
  page.on('pageerror', error => errors.push(error.message));
  const networkErrors = [];
  page.on('requestfailed', req => networkErrors.push(`${req.url()} ${req.failure()?.errorText}`));
  page.on('response', res => { if (res.status() >= 400) networkErrors.push(`${res.status()} ${res.url()}`); });
  const projectId = 'a0000000-0000-4000-8000-000000000001';
  const assetId = 'b0000000-0000-4000-8000-000000000001';
  let role = 'annotator', timed = false, failAnalytics = false;
  const project = { id: projectId, project_name: '分析验收项目', tenant_id: 1,
    task_type: 'detection', status: 'active', label_schema: { labels: ['人'] } };
  const asset = { id: assetId, project_id: projectId, original_name: 'fixture.png', width: 64, height: 48,
    annotation_status: 'completed', blob_path: 'fixture.png', format: 'PNG' };
  const latest = { id: 'c0000000-0000-4000-8000-000000000002', asset_id: assetId, version: 2,
    is_latest: true, review_status: 'pending', annotation_source: 'manual', annotation_data: { boxes: [] } };
  const old = { ...latest, id: 'c0000000-0000-4000-8000-000000000001', version: 1, is_latest: false, review_status: 'rejected' };
  const fixture = () => ({
    quality: { total: 3, approved: 1, rejected: 1, pending: 1, negative_samples: 1,
      sources: { manual: 2, model: 1, unknown: 0 }, review_origins: { human: 1, automatic: 1, unknown: 0 },
      process_pass_rate: .5, categories: [{ label: '人', boxes: 5, assets: 2 }] },
    workload: { human_annotation_events: 7, automatic_annotation_events: 12, human_review_events: 3,
      automatic_review_events: 9, rework_events: 2, unknown_events: 0 },
    efficiency: { timing_status: timed ? 'collected' : 'not_collected', active_seconds: timed ? 60 : null,
      annotation_seconds: timed ? 45 : null, review_seconds: timed ? 15 : null,
      timed_annotation_events: timed ? 1 : 0, timed_review_events: timed ? 1 : 0,
      annotation_per_hour: timed ? 80 : null, review_per_hour: timed ? 240 : null,
      users: timed ? [{ user_id: 1, username: 'fixture', active_seconds: 60, annotation_seconds: 45, review_seconds: 15,
        annotation_events: 7, review_events: 3, timed_annotation_events: 1, timed_review_events: 1,
        annotation_per_hour: 80, review_per_hour: 240 }] : [] },
    definitions: { quality: '当前流程不是独立测试集准确率。', workload: '人工与自动事件分别计数。',
      timing: '同一用户跨标签页按区间并集，模式时长不可相加。', efficiency: '只有计时内人工事件进入每小时效率。' },
  });
  const handler = async route => {
    const req = route.request(), url = new URL(req.url()), method = req.method(), p = url.pathname;
    if (!p.startsWith('/api/')) return route.continue();
    if (p === '/api/analytics/activity' && method === 'POST') {
      heartbeats.push(req.postDataJSON());
      return route.fulfill({ json: { duplicate: false, accepted_seconds: 15 } });
    }
    if (!['GET', 'HEAD', 'OPTIONS'].includes(method)) {
      unexpectedWrites.push(`${method} ${p}`); return route.abort();
    }
    if (p === '/api/analytics') {
      queries.push(Object.fromEntries(url.searchParams));
      return route.fulfill(failAnalytics ? { status: 503, json: { detail: 'fixture unavailable' } } : { json: fixture() });
    }
    if (p === '/api/auth/me') return route.fulfill({ json: { id: 1, tenant_id: 1, username: 'fixture', role } });
    if (p === '/api/projects') return route.fulfill({ json: { items: [project], total: 1 } });
    if (p === `/api/projects/${projectId}`) return route.fulfill({ json: project });
    if (p === `/api/assets/${assetId}`) return route.fulfill({ json: asset });
    if (p === `/api/assets/${assetId}/neighbors`) return route.fulfill({ json: { position: 1, total: 1, previous_id: null, next_id: null } });
    if (p === `/api/assets/${assetId}/content`) return route.fulfill({ contentType: 'image/png',
      body: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a9WQAAAAASUVORK5CYII=', 'base64') });
    if (p === '/api/annotations') return route.fulfill({ json: { items: [latest, old], total: 2 } });
    if (p.endsWith('/events') || p === '/api/model-services') return route.fulfill({ json: [] });
    return route.fulfill({ status: 404, json: { detail: `Unmocked read ${p}` } });
  };
  await page.route('**/api/**', handler);
  await page.addInitScript(() => {
    if (!['http:', 'https:'].includes(location.protocol)) return;
    if (!localStorage.getItem('token')) {
      localStorage.setItem('token', 'fixture-only');
      localStorage.setItem('user', JSON.stringify({ id: 1, tenant_id: 1, username: 'fixture', role: 'annotator' }));
    }
  });
  await page.setViewportSize({ width: 1440, height: 1100 });
  try {
    await page.goto(`${origin}/analytics?project_id=${projectId}`);
    await page.getByText('此范围内活跃时长未采集，无法计算人工效率；旧数据不会按创建时间差估算。', { exact: true }).waitFor();
    await page.getByText('流程通过率不等于独立测试集准确率；这里没有独立真值评测。', { exact: true }).waitFor();
    assert.equal(await page.locator('.ant-statistic').filter({ hasText: '模型自动生成（次）' }).locator('.ant-statistic-content').innerText(), '12');
    checks.push('quality definitions, auto/manual separation and uncollected legacy time render');
    await page.getByLabel('统计开始日期').fill('2026-10-10');
    await page.getByLabel('统计结束日期').fill('2026-10-11');
    const filterResponse = page.waitForResponse(r => new URL(r.url()).pathname === '/api/analytics');
    await page.getByRole('button', { name: '查询统计' }).click();
    await filterResponse;
    assert.equal(queries.at(-1).project_id, projectId);
    assert.equal(queries.at(-1).start, '2026-10-09T16:00:00.000Z');
    assert.equal(queries.at(-1).end, '2026-10-11T16:00:00.000Z');
    checks.push('project/date filters send local-inclusive days as UTC half-open range');
    timed = true;
    await page.getByRole('button', { name: '查询统计' }).click();
    await page.getByRole('cell', { name: '80.0 次/小时', exact: true }).waitFor();
    fs.mkdirSync(path.resolve(__dirname, '../output/playwright'), { recursive: true });
    await page.screenshot({ path: path.resolve(__dirname, '../output/playwright/annotation-analytics.png'), fullPage: true });
    checks.push('observed manual effort and rates render in user table');
    failAnalytics = true;
    await page.getByRole('button', { name: '查询统计' }).click();
    await page.getByText('统计读取失败，请重试', { exact: true }).waitFor();
    assert.equal(await page.getByRole('cell', { name: '80.0 次/小时', exact: true }).count(), 0);
    checks.push('failed refresh clears previous statistics and displays error');
    failAnalytics = false;

    await page.goto(`${origin}/annotate/${assetId}`);
    await page.getByRole('img', { name: '检测画框区域' }).waitFor();
    assert.equal(await page.getByRole('button', { name: '审核通过', exact: true }).isDisabled(), true);
    assert.equal(await page.getByRole('button', { name: /^驳\s*回$/ }).isDisabled(), true);
    await page.getByText('负样本须由管理员最终审核，当前账号无法通过或驳回。', { exact: true }).waitFor();
    checks.push('non-admin cannot approve or reject saved negative sample');
    role = 'admin';
    await page.evaluate(() => localStorage.setItem('user', JSON.stringify({ id: 1, tenant_id: 1, username: 'fixture', role: 'admin' })));
    await page.clock.install();
    await page.reload();
    await page.bringToFront();
    await page.getByRole('img', { name: '检测画框区域' }).waitFor();
    assert.equal(await page.getByRole('button', { name: '审核通过', exact: true }).isEnabled(), true);
    checks.push('admin retains final negative-sample review actions');

    const before = heartbeats.length;
    await page.clock.fastForward(15000);
    assert.equal(heartbeats.length, before);
    const canvas = page.getByRole('img', { name: '检测画框区域' });
    assert.equal(await page.evaluate(() => document.hasFocus()), true, 'test browser must be focused');
    await canvas.focus(); await page.keyboard.press('Escape');
    await page.getByText('正在采集活跃时间', { exact: true }).waitFor();
    const heartbeatResponse = page.waitForResponse(r => new URL(r.url()).pathname === '/api/analytics/activity');
    await page.clock.fastForward(15000); await heartbeatResponse;
    assert.equal(heartbeats.at(-1).activity, 'annotation');
    const elapsed = Date.parse(heartbeats.at(-1).ended_at)-Date.parse(heartbeats.at(-1).started_at);
    assert.ok(elapsed >= 14000 && elapsed <= 16000, `expected one ~15s interval, received ${elapsed}ms`);
    await page.getByText('审核计时', { exact: true }).click();
    await page.getByText('等待工作台操作后计时；空闲60秒暂停', { exact: true }).waitFor();
    await canvas.focus(); await page.keyboard.press('Escape');
    const reviewResponse = page.waitForResponse(r => new URL(r.url()).pathname === '/api/analytics/activity'
      && r.request().postDataJSON().activity === 'review');
    await page.clock.fastForward(15000); await reviewResponse;
    assert.equal(heartbeats.at(-1).activity, 'review');
    checks.push('real workbench interaction sends separate annotation/review activity intervals');

    await page.evaluate(() => {
      Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' });
      document.dispatchEvent(new Event('visibilitychange'));
    });
    await page.clock.fastForward(1000);
    const hiddenCount = heartbeats.length;
    await page.clock.fastForward(90000);
    assert.equal(heartbeats.length, hiddenCount);
    await page.evaluate(() => {
      delete document.visibilityState; document.dispatchEvent(new Event('visibilitychange'));
    });
    checks.push('hidden workbench stops accumulating activity');
    await page.getByLabel('标注历史版本').click();
    await page.getByText('v1 · 已驳回', { exact: true }).click();
    await page.getByText('当前为历史版本，只读。', { exact: false }).waitFor();
    await page.clock.fastForward(1000);
    const historyCount = heartbeats.length;
    await canvas.focus(); await page.keyboard.press('Escape');
    await page.clock.fastForward(90000);
    assert.equal(heartbeats.length, historyCount);
    checks.push('historical read-only version never records activity');
    assert.deepEqual(unexpectedWrites, []);
    assert.deepEqual(errors, []);
    return { checks, heartbeats: heartbeats.length, scope: 'all API responses mocked; zero business writes', errors };
  } catch (error) {
    console.error(JSON.stringify({ browserErrors: errors, networkErrors, url: page.url(), body: await page.locator('body').innerText() }));
    throw error;
  } finally {
    // Stop tracker while the write guard is still installed.
    await page.goto('about:blank');
    await page.unroute('**/api/**', handler);
  }
}

module.exports = verify;
if (require.main === module) {
  const bundled = path.join(process.env.USERPROFILE || '', '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
  let playwright;
  try { playwright = require('playwright'); } catch { playwright = require(bundled); }
  (async () => {
    const browser = await playwright.chromium.launch({ channel: 'msedge', headless: true });
    try {
      const context = await browser.newContext({ timezoneId: 'Asia/Shanghai', serviceWorkers: 'block' });
      const result = await verify(await context.newPage(), process.argv[2]);
      assert.deepEqual(result.errors, []);
      fs.writeFileSync(path.resolve(__dirname, '../output/playwright/annotation-analytics-verification.json'), JSON.stringify(result, null, 2));
      console.log(JSON.stringify(result, null, 2));
    } finally { await browser.close(); }
  })().catch(error => { console.error(error); process.exitCode = 1; });
}
