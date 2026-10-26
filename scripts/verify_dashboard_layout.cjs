async page => {
  const checks = [], errors = [], requests = [];
  let phase = 'normal';
  const project = { id: '11111111-1111-4111-8111-111111111111', project_name: '误报复核验收项目', task_type: 'detection', total_assets: 20, annotated_count: 12, reviewed_count: 8, created_at: '2026-10-16T08:00:00Z' };
  const images = Array.from({ length: 4 }, (_, i) => ({ id: `image-${i}`, original_name: `场景-${i + 1}.jpg`, collection_names: ['车间图片集'], scene_labels: ['车间作业'], scene_status: 'completed', created_at: project.created_at }));
  const servers = [{ id: 'server-1', name: '验收服务器', enabled: true }];
  const snapshot = { server_name: '验收服务器', collected_at: '2026-10-16T09:00:00Z', cached: false, warnings: [], processes_complete: true,
    gpus: [{ index: 0, uuid: 'gpu-0', name: '验收GPU', utilization_percent: 61, memory_used_mib: 12698, memory_total_mib: 24576, temperature_c: 58 },
      { index: 1, uuid: 'gpu-1', name: '未采集GPU', utilization_percent: null, memory_used_mib: null, memory_total_mib: null, temperature_c: null }],
    processes: [{ gpu_uuid: 'gpu-0', pid: 1 }, { gpu_uuid: 'gpu-0', pid: 2 }] };
  const pagination = items => ({ items, total: items.length, page: 1, page_size: 4 });
  const assert = (condition, message) => { if (!condition) throw Error(message); };
  await page.unrouteAll();
  page.on('pageerror', error => errors.push(error.message));
  await page.route(/^http:\/\/localhost:3000\/api\//, async route => {
    const req = route.request(), path = new URL(req.url()).pathname;
    requests.push({ path, method: req.method() });
    if (req.method() !== 'GET') return route.fulfill({ status: 405, json: { detail: 'UI验证禁止写入' } });
    if (path.endsWith('/content')) return route.fulfill({ path: `output/ui-redesign/assets/sample-${Number(path.match(/image-(\d)/)?.[1] || 0) + 1}.jpg` });
    if (phase === 'error' && ['/api/stats/dashboard', '/api/images', '/api/validation/runs'].includes(path)) return route.fulfill({ status: 503, json: { detail: '受控读取失败' } });
    let data;
    if (path === '/api/projects') data = pagination(phase === 'empty' ? [] : [project]);
    else if (path === '/api/stats/dashboard') data = { projects_overview: { total: phase === 'empty' ? 0 : 1 }, assets_overview: { total: phase === 'empty' ? 0 : 80, project_references: phase === 'empty' ? 0 : 120 }, annotations_overview: { total: phase === 'empty' ? 0 : 60, approved: phase === 'empty' ? 0 : 40, pending_review: phase === 'empty' ? 0 : 20 } };
    else if (path === '/api/images') data = pagination(phase === 'empty' ? [] : images);
    else if (path === '/api/automation/jobs') data = pagination(phase === 'empty' ? [] : [{ id: 'auto-1', project_id: project.id, kind: 'preannotate', status: 'running', total: 10, processed: 4, created_at: '2026-10-16T10:00:00Z' }]);
    else if (path === '/api/image-semantics/jobs') data = phase === 'empty' ? [] : [{ id: 'sem-1', total: 10, counts: { completed: 6, running: 1, pending: 3 }, created_at: '2026-10-16T09:00:00Z' }];
    else if (path === '/api/validation/runs') data = pagination(phase === 'empty' ? [] : [{ id: 'val-1', name: '夜间验证', status: 'completed', total: 10, processed: 10, created_at: '2026-10-16T08:00:00Z' }]);
    else if (path === '/api/training/runs') data = pagination([]);
    else if (path === '/api/gpu-resources/servers') data = phase === 'empty' ? [] : servers;
    else if (path === '/api/gpu-resources/servers/server-1/resources') data = snapshot;
    else return route.fulfill({ status: 404, json: { detail: `未配置的验收接口 ${path}` } });
    return route.fulfill({ json: data });
  });
  await page.goto('http://localhost:3000/login');
  await page.evaluate(() => {
    localStorage.setItem('token', 'isolated-dashboard-fixture');
    localStorage.setItem('user', JSON.stringify({ username: '界面验收', role: 'admin' }));
  });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.setViewportSize({ width: 1440, height: 1100 });
  await page.goto('http://localhost:3000/');
  for (const name of ['当前工作', '图片巡览', '处理队列', '算力概况']) await page.getByRole('heading', { name, exact: true }).waitFor({ timeout: 5000 });
  await page.getByText(project.project_name, { exact: true }).waitFor();
  await page.getByRole('button', { name: '预览 场景-1.jpg', exact: true }).waitFor();
  await page.getByText('12.4 / 24.0 GB', { exact: true }).waitFor();
  assert(await page.locator('.dashboard-metric').count() === 4, 'Four metrics missing');
  assert(await page.locator('.dashboard-metric').nth(3).innerText().then(text => text.includes('60')), 'Unannotated count used library total instead of project references');
  assert(await page.locator('.dashboard-current').getByText('40%', { exact: true }).count() === 1, 'Current project progress mixed with global annotations');
  assert((await page.getByRole('link', { name: '查看待审图片 →', exact: true }).getAttribute('href')).includes('review_status=pending'), 'Review shortcut did not filter pending annotations');
  assert(await page.locator('.dashboard-queue-row').count() === 3, 'Queue did not combine real task sources');
  assert(await page.getByText('最近项目', { exact: true }).count() === 0 && await page.getByText('开始工作', { exact: true }).count() === 0, 'Old dashboard panels remain');
  checks.push('four metrics and four panels; project/global counting stays separate; real task sources combined');
  for (const width of [2320, 1440, 768, 390]) {
    await page.setViewportSize({ width, height: width > 1000 ? 1100 : 844 });
    await page.locator('.dashboard-shell').evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    const dimensions = await page.evaluate(() => {
      const bounds = name => document.querySelector(name).getBoundingClientRect().toJSON();
      return { width: innerWidth, content: document.documentElement.scrollWidth, current: bounds('.dashboard-current'), gallery: bounds('.dashboard-gallery'), queue: bounds('.dashboard-queue'), gpu: bounds('.dashboard-compute') };
    });
    assert(dimensions.content <= width + 1, `Overflow at ${width}`);
    if (width >= 1000) assert(Math.abs(dimensions.current.y - dimensions.gallery.y) < 2 && Math.abs(dimensions.queue.y - dimensions.gpu.y) < 2, 'Desktop panel pairs not aligned');
    else assert(dimensions.gallery.y > dimensions.current.y && dimensions.queue.y > dimensions.gallery.y && dimensions.gpu.y > dimensions.queue.y, 'Small-screen reading order incorrect');
    await page.screenshot({ path: `output/playwright/dashboard-layout-${width}.png`, fullPage: true });
    checks.push(`${width}px layout and no horizontal overflow`);
  }
  await page.getByRole('button', { name: '预览 场景-1.jpg', exact: true }).click();
  await page.locator('.asset-paged-preview').waitFor();
  await page.keyboard.press('ArrowRight');
  await page.getByRole('img', { name: '场景-2.jpg', exact: true }).last().waitFor();
  await page.keyboard.press('Escape');
  await page.locator('.asset-paged-preview').waitFor({ state: 'hidden' });
  checks.push('existing authenticated image preview, arrow navigation and Escape');
  await page.getByRole('combobox', { name: '概览显卡', exact: true }).click();
  await page.getByTitle('GPU 1 · 未采集GPU', { exact: true }).click();
  assert(await page.locator('.dashboard-gauge').getByText('--', { exact: true }).count() === 1, 'Missing GPU metric was shown as zero');
  checks.push('GPU card uses selected GPU; unknown utilization remains unknown');
  phase = 'error';
  await page.reload();
  await page.getByText(/统计读取失败/).waitFor();
  await page.getByText('图片读取失败，请重试概览', { exact: true }).waitFor();
  await page.getByText(/部分任务读取失败/).waitFor();
  assert(await page.locator('.dashboard-queue-row').count() > 0, 'Partial task failure hid successful sources');
  phase = 'normal';
  await page.getByRole('button', { name: '重试概览', exact: true }).click();
  await page.getByRole('button', { name: '预览 场景-1.jpg', exact: true }).waitFor();
  checks.push('independent error states, partial task data and overview retry');
  phase = 'empty';
  await page.reload();
  for (const text of ['暂无标注项目', '图片库暂无图片', '暂无处理任务', '暂无已启用的GPU服务器']) await page.getByText(text, { exact: true }).waitFor();
  checks.push('empty states without demo images, jobs or GPU metrics');
  phase = 'normal';
  await page.evaluate(() => localStorage.setItem('user', JSON.stringify({ username: '普通用户', role: 'user' })));
  const before = requests.length;
  await page.reload();
  await page.getByText('仅管理员可查看算力状态', { exact: true }).waitFor();
  assert(!requests.slice(before).some(req => req.path.startsWith('/api/gpu-resources') || req.path.startsWith('/api/training/')), 'Normal user fetched administrator resources');
  assert(!requests.some(req => req.method !== 'GET'), 'Dashboard triggered a write');
  assert(!errors.length, `Runtime errors: ${errors.join('; ')}`);
  checks.push('normal-user permissions; no administrator resource reads, writes or runtime errors');
  return { checks, runtimeErrors: errors, requests: requests.length, writeRequests: 0, scope: 'All APIs mocked; real component rendering and local image bytes; no business or remote writes.' };
}
