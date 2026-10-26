async (page) => {
  const checks = [], errors = [], blockedWrites = [];
  const projectId = '11111111-1111-4111-8111-111111111111';
  const assetId = '22222222-2222-4222-8222-222222222222';
  const project = { id: projectId, project_name: '钴蓝界面验收项目', task_type: 'detection', description: '隔离浏览器样本，不写入业务数据', label_schema: { labels: ['person'] }, status: 'active', total_assets: 1, annotated_count: 1, reviewed_count: 0, created_at: '2026-10-16T08:00:00Z' };
  const asset = { id: assetId, project_id: projectId, image_id: assetId, original_name: 'UI验收图片.jpg', width: 640, height: 360, file_size: 12000, format: 'JPEG', source_type: 'upload', annotation_status: 'completed', created_at: project.created_at, updated_at: project.created_at, collection_names: ['UI验收集'], scene_labels: ['车间'], scene_status: 'completed' };
  const annotation = { id: 'ui-annotation', asset_id: assetId, version: 1, is_latest: true, review_status: 'pending', annotation_data: { boxes: [{ label: 'person', x: 200, y: 100, width: 100, height: 140 }] }, created_at: project.created_at };
  const paginated = items => ({ items, total: items.length, page: 1, page_size: 20 });
  const assert = (condition, message) => { if (!condition) throw new Error(message); };
  await page.unrouteAll();
  page.on('pageerror', error => errors.push(error.message));
  await page.route(/^http:\/\/localhost:3000\/api\//, async route => {
    const request = route.request(), path = new URL(request.url()).pathname;
    if (request.method() !== 'GET') {
      blockedWrites.push(path);
      return route.fulfill({ json: { id: 'ui-session', status: 'active' } });
    }
    if (path.endsWith('/content')) return route.fulfill({ path: 'output/ui-redesign/assets/sample-3.jpg' });
    let data;
    if (path === '/api/projects') data = paginated([project]);
    else if (path === `/api/projects/${projectId}`) data = project;
    else if (path === '/api/stats/dashboard') data = { projects_overview: { total: 1 }, assets_overview: { total: 1 }, annotations_overview: { total: 1, approved: 0 } };
    else if (path === '/api/images/collections') data = [{ id: 'ui-collection', name: 'UI验收集', image_count: 1 }];
    else if (path === '/api/images' || path === '/api/assets') data = paginated([asset]);
    else if (path === `/api/assets/${assetId}`) data = asset;
    else if (path.endsWith('/neighbors')) data = { asset_id: assetId, project_id: projectId, total: 1, position: 1, previous_id: null, next_id: null };
    else if (path === '/api/annotations') data = paginated([annotation]);
    else if (path.endsWith('/events') || path === '/api/model-services' || path.endsWith('/servers') || path === '/api/image-semantics/jobs') data = [];
    else if (path === '/api/training/catalog') data = {};
    else if (path.startsWith('/api/smarteye/')) data = { code: 0, data: [] };
    else if (path.endsWith('/runs') || path.endsWith('/sets') || path.endsWith('/jobs')) data = paginated([]);
    else return route.fulfill({ status: 503, json: { detail: 'UI验收：此接口仅检查错误状态样式' } });
    return route.fulfill({ json: data });
  });
  await page.goto('http://localhost:3000/login');
  await page.evaluate(() => {
    localStorage.setItem('token', 'isolated-ui-verification');
    localStorage.setItem('user', JSON.stringify({ username: '界面验收', role: 'admin' }));
  });
  await page.emulateMedia({ reducedMotion: 'no-preference' });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto('http://localhost:3000/');
  await page.getByText('钴蓝界面验收项目', { exact: true }).waitFor();
  assert(await page.locator('html').evaluate(el => getComputedStyle(el).getPropertyValue('--ui-background').trim()) === '#0b101b', 'A cobalt background has not been applied');
  assert(await page.getByText('数据资产', { exact: true }).isVisible() && await page.locator('.ant-menu-item-group').count() === 3, 'Grouped navigation missing');
  assert(await page.locator('.platform-content > :last-child').evaluate(el => getComputedStyle(el).animationName !== 'none'), 'Page entrance animation missing');
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.waitForFunction(() => getComputedStyle(document.querySelector('.platform-content > :last-child')).animationName === 'none');
  assert(await page.locator('.platform-content > :last-child').evaluate(el => getComputedStyle(el).animationName === 'none'), 'Reduced motion does not disable entrance');
  checks.push('cobalt tokens, grouped navigation, entrance animation and reduced-motion fallback');
  const fit = async (name, root = page) => {
    await root.locator('html').evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    const metrics = await root.locator('html').evaluate(el => ({ width: innerWidth, content: el.scrollWidth, background: getComputedStyle(el).getPropertyValue('--ui-background').trim(), scheme: getComputedStyle(el).colorScheme }));
    assert(metrics.content <= metrics.width + 1, `${name}: horizontal overflow ${JSON.stringify(metrics)}`);
    assert(metrics.background === '#0b101b' && metrics.scheme === 'dark', `${name}: theme mismatch`);
    checks.push(name);
  };
  for (const [path, title] of [['/', '工作概览'], ['/projects', '项目管理'], ['/assets', '图片管理'], ['/annotations', '标注与审核'], ['/models', '模型管理'], ['/validation', '验证集'], ['/training', '模型训练'], ['/gpu-resources', 'GPU资源'], ['/gpu-terminal', 'SSH调试'], ['/automation', '预标注与AI审核'], ['/image-semantics', '图片描述与检索'], ['/analytics', '质量与效率分析'], ['/storage-maintenance', '存储维护']]) {
    await page.goto('http://localhost:3000' + path);
    await page.getByRole('heading', { name: title, exact: true }).first().waitFor();
    for (const width of [1440, 768, 390]) {
      await page.setViewportSize({ width, height: width === 1440 ? 1000 : 844 });
      await fit(`${path} at ${width}px`);
    }
    if (['/', '/assets', '/training'].includes(path)) {
      await page.screenshot({ path: `output/playwright/cobalt-${path.slice(1) || 'dashboard'}-mobile.png`, fullPage: true });
      await page.setViewportSize({ width: 1440, height: 1000 });
      await page.screenshot({ path: `output/playwright/cobalt-${path.slice(1) || 'dashboard'}-desktop.png`, fullPage: true });
    }
  }
  await page.goto('http://localhost:3000/projects');
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.getByRole('button', { name: /新建项目/ }).click();
  const dialog = page.getByRole('dialog', { name: '新建项目', exact: true });
  await dialog.waitFor();
  await page.waitForFunction(() => {
    const modal = document.querySelector('.ant-modal');
    return modal && getComputedStyle(modal).opacity === '1' && getComputedStyle(modal).transform === 'none';
  }, undefined, { timeout: 1000 });
  assert(await dialog.evaluate(el => getComputedStyle(el).opacity === '1' && getComputedStyle(el).transform === 'none'), 'Reduced-motion modal is visually hidden or still moving');
  const modalBackground = await page.locator('.ant-modal-container').evaluate(el => getComputedStyle(el).backgroundColor);
  assert(modalBackground !== 'rgb(255, 255, 255)', 'Modal stayed white');
  await dialog.getByLabel('项目名称', { exact: true }).fill('不提交的界面检查');
  await page.screenshot({ path: 'output/playwright/cobalt-project-modal.png' });
  await page.keyboard.press('Escape');
  await dialog.waitFor({ state: 'hidden' });
  checks.push('project form is editable; dark modal and Escape close');
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole('button', { name: '展开导航', exact: true }).click();
  const navigation = page.getByRole('dialog', { name: '平台导航', exact: true });
  await navigation.waitFor();
  await navigation.getByRole('menuitem', { name: '图片管理', exact: true }).click();
  await navigation.waitFor({ state: 'hidden' });
  await page.getByRole('heading', { name: '图片管理', exact: true }).waitFor();
  await page.getByRole('button', { name: '展开导航', exact: true }).click();
  await navigation.waitFor();
  await page.keyboard.press('Escape');
  await navigation.waitFor({ state: 'hidden' });
  checks.push('mobile drawer navigates and closes');
  await page.setViewportSize({ width: 1366, height: 768 });
  await page.goto(`http://localhost:3000/annotate/${assetId}`);
  const canvas = page.getByRole('img', { name: '检测画框区域', exact: true });
  await canvas.waitFor();
  await canvas.evaluate(el => { el.uiIdentity = true; });
  await page.emulateMedia({ reducedMotion: 'no-preference' });
  assert(await canvas.evaluate(el => el.uiIdentity === true), 'System motion preference remounted canvas');
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.getByRole('button', { name: '收起导航', exact: true }).click();
  assert(await canvas.evaluate(el => el.uiIdentity === true), 'Navigation collapse remounted canvas');
  const geometry = await canvas.evaluate(el => ({ bottom: el.getBoundingClientRect().bottom, viewport: innerHeight, content: document.documentElement.scrollHeight, animated: [...document.querySelectorAll('.platform-content, .detection-workspace, .platform-main')].some(node => getComputedStyle(node).transform !== 'none' || getComputedStyle(node).animationName !== 'none') }));
  assert(!geometry.animated && geometry.bottom <= geometry.viewport + 1 && geometry.content <= geometry.viewport + 1, `Canvas layout changed ${JSON.stringify(geometry)}`);
  await page.screenshot({ path: 'output/playwright/cobalt-workspace.png' });
  checks.push('annotation canvas keeps identity, has no ancestor animation and fits viewport');
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto('http://localhost:3000/smarteye');
  const frame = page.frameLocator('iframe[title="SmartEye 数据拉取"]');
  await frame.getByRole('navigation', { name: 'SmartEye 功能导航' }).waitFor();
  for (const name of ['检测查询', '台账查询', '台账下载', '智搜下载', 'AI 生图', '工具任务']) {
    await frame.getByRole('navigation').getByText(name, { exact: true }).click();
    await fit(`SmartEye ${name}`, frame);
    const inputScheme = await frame.locator('input:not([type=checkbox]):visible').first().evaluate(el => getComputedStyle(el).colorScheme);
    assert(inputScheme === 'dark', `${name}: light native control`);
    if (name === '检测查询') assert(await frame.getByRole('button', { name: /同步配置/ }).evaluate(el => getComputedStyle(el).backgroundColor !== 'rgb(255, 255, 255)'), 'Legacy secondary button stayed white');
    await page.screenshot({ path: `output/playwright/cobalt-smarteye-${name}.png` });
  }
  await frame.getByRole('button', { name: /任务记录/ }).click();
  await frame.getByRole('dialog', { name: 'SmartEye 任务记录' }).waitFor();
  await page.keyboard.press('Escape');
  await frame.getByRole('dialog', { name: 'SmartEye 任务记录' }).waitFor({ state: 'hidden' });
  await page.goto('http://localhost:3000/');
  await page.emulateMedia({ reducedMotion: 'no-preference' });
  await page.getByRole('menuitem', { name: '项目管理', exact: true }).click();
  await page.getByRole('heading', { name: '项目管理', exact: true }).waitFor();
  assert(await page.locator('.platform-content > :last-child').evaluate(el => getComputedStyle(el).animationName === 'ui-enter'), 'Normal motion did not resume after system preference changed');
  await page.goto('http://localhost:3000/');
  await page.evaluate(() => localStorage.setItem('user', JSON.stringify({ username: '普通用户验收', role: 'user' })));
  await page.reload();
  assert(await page.getByRole('menuitem', { name: 'GPU资源', exact: true }).count() === 0, 'Admin entry leaked to normal user');
  assert(await page.getByRole('menuitem', { name: '图片管理', exact: true }).isVisible(), 'Normal user lost image entry');
  assert(!errors.length, `Runtime errors: ${errors.join('; ')}`);
  return { checks, blockedWrites, runtimeErrors: errors, scope: 'All API calls intercepted. No backend writes, inference, training, remote GPU or upstream platform requests.' };
}
