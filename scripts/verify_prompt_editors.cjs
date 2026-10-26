async page => {
  const checks = [], writes = [], errors = [];
  let failSave = false;
  const defaults = { caption: '默认描述指令', review: '默认完整审核指令', scene: '默认场景分类指令' };
  let model = { id: '11111111-1111-4111-8111-111111111111', key: 'qwen', name: 'Qwen3.8-27B', kind: 'caption', protocol: 'openai_chat', base_url: 'http://model.test/v1', endpoint: '/chat/completions', model_name: 'Qwen3.8-27B', enabled: true, revision: 1, timeout_seconds: 60, confidence: .25, iou: .45, labels: [], label_mapping: {}, api_key_env: '', chat_prompts: { caption: '已保存描述', review: '已保存审核' } };
  let scene = { classification_enabled: true, base_url: model.base_url, model: model.model_name, scenes: ['工厂'], min_long_edge: 720, scene_prompt: '已保存场景' };
  const assert = (value, message) => { if (!value) throw Error(message); checks.push(message); };
  await page.unrouteAll();
  page.on('pageerror', error => errors.push(error.message));
  await page.route(/^http:\/\/localhost:3000\/api\//, async route => {
    const req = route.request(), path = new URL(req.url()).pathname;
    const json = (data, status = 200) => route.fulfill({ status, json: data });
    if (path === '/api/auth/me') return json({ id: 1, role: 'admin', username: '隔离验收' });
    if (path === '/api/model-services/prompt-defaults') return json(defaults);
    if (path === '/api/model-services') return json([model]);
    if (path === `/api/model-services/${model.id}` && req.method() === 'PUT') {
      writes.push({ path, body: req.postDataJSON() });
      if (failSave) return json({ detail: '隔离保存失败' }, 503);
      model = { ...model, ...req.postDataJSON(), revision: model.revision + 1 }; return json(model);
    }
    if (path === '/api/images/ingest-settings') {
      if (req.method() === 'PUT') { writes.push({ path, body: req.postDataJSON() }); scene = req.postDataJSON(); }
      return json(scene);
    }
    if (path === '/api/images/collections') return json([{ id: 'collection-1', name: '验收图片集', image_count: 0 }]);
    if (path === '/api/images') return json({ items: [{ id: '22222222-2222-4222-8222-222222222222', original_name: '提示词验收.png', width: 32, height: 24, scene_labels: [], collection_names: [] }], total: 1, page: 1, page_size: 20 });
    if (path.endsWith('/content')) return route.fulfill({ path: 'output/ui-redesign/assets/sample-1.jpg' });
    if (path === '/api/projects') return json({ items: [], total: 0, page: 1, page_size: 20 });
    if (path === '/api/image-semantics/jobs') {
      if (req.method() === 'POST') { writes.push({ path, body: req.postDataJSON() }); return json({ id: 'batch-1', total: 1, counts: { pending: 1 } }, 201); }
      return json([]);
    }
    if (path === '/api/image-semantics/jobs/batch-1') return json({ id: 'batch-1', total: 1, counts: { pending: 1 }, items: [], page: 1 });
    return json({ detail: '未配置验收接口' }, 404);
  });
  await page.goto('http://localhost:3000/login');
  await page.evaluate(() => { localStorage.setItem('token', 'prompt-fixture'); localStorage.setItem('user', JSON.stringify({ username: '隔离验收', role: 'admin' })); });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto('http://localhost:3000/models');
  await page.getByRole('button', { name: '编辑', exact: true }).click();
  let dialog = page.getByRole('dialog', { name: '编辑模型', exact: true });
  const caption = dialog.getByRole('textbox', { name: '描述核心提示词', exact: true });
  const review = dialog.getByRole('textbox', { name: '审核核心提示词', exact: true });
  await caption.waitFor({ timeout: 5000 });
  assert(await caption.inputValue() === '已保存描述' && await review.inputValue() === '已保存审核', 'saved complete instructions load');
  await caption.fill('只描述设备位置'); await review.fill('重点核对遮挡目标');
  await dialog.getByRole('button', { name: '保存模型', exact: true }).click();
  await dialog.waitFor({ state: 'hidden' });
  assert(writes.at(-1).body.chat_prompts.caption === '只描述设备位置' && writes.at(-1).body.chat_prompts.review === '重点核对遮挡目标', 'both instructions submitted with model config');
  await page.getByRole('button', { name: '编辑', exact: true }).click();
  assert(await caption.inputValue() === '只描述设备位置', 'saved instruction survives reopen');
  await dialog.getByRole('button', { name: '恢复默认描述提示词', exact: true }).click();
  assert(await caption.inputValue() === defaults.caption && model.chat_prompts.caption === '只描述设备位置', 'restore changes draft only until save');
  failSave = true;
  await review.fill('保存失败仍保留的编辑');
  await dialog.getByRole('button', { name: '保存模型', exact: true }).click();
  await dialog.getByText('保存失败，请检查模型配置；当前输入已保留', { exact: true }).waitFor();
  assert(await review.inputValue() === '保存失败仍保留的编辑', 'failed save retains input');
  failSave = false;
  await dialog.getByRole('button', { name: '恢复默认审核提示词', exact: true }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  await review.scrollIntoViewIfNeeded();
  await page.screenshot({ path: 'output/playwright/prompt-editor-mobile.png', fullPage: true });
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'mobile editor has no page overflow');
  await dialog.getByRole('button', { name: '保存模型', exact: true }).click(); await dialog.waitFor({ state: 'hidden' });
  await page.goto('http://localhost:3000/assets');
  await page.getByRole('combobox', { name: '筛选图片集', exact: true }).click();
  await page.locator('.ant-select-dropdown:visible').getByText('验收图片集（0）', { exact: true }).click();
  await page.getByRole('button', { name: '图片集设置', exact: true }).click();
  dialog = page.getByRole('dialog', { name: '图片集入库与场景设置', exact: true });
  const scenePrompt = dialog.getByRole('textbox', { name: '场景分类提示词', exact: true });
  await scenePrompt.waitFor(); assert(await scenePrompt.inputValue() === '已保存场景', 'scene prompt loads from collection config');
  await scenePrompt.fill('场景分类自定义要求');
  await dialog.getByRole('button', { name: '保存设置', exact: true }).click(); await dialog.waitFor({ state: 'hidden' });
  assert(writes.at(-1).body.scene_prompt === '场景分类自定义要求', 'scene prompt saved with collection settings');
  await page.getByRole('button', { name: '图片集设置', exact: true }).click();
  await dialog.getByRole('button', { name: '恢复默认场景提示词', exact: true }).click();
  assert(await scenePrompt.inputValue() === defaults.scene && scene.scene_prompt === '场景分类自定义要求', 'scene restore remains unsaved draft');
  await page.goto('http://localhost:3000/image-semantics');
  await page.getByRole('combobox', { name: '描述模型', exact: true }).click();
  await page.locator('.ant-select-dropdown:visible').getByText(`Qwen3.8-27B · r${model.revision}`, { exact: true }).click();
  await page.getByRole('textbox', { name: '本批描述提示词', exact: true }).fill('本批只描述车辆');
  await page.getByRole('row', { name: /提示词验收.png/ }).getByRole('checkbox').check();
  await page.getByRole('button', { name: '生成所选 1 张', exact: true }).click();
  await page.getByText('已创建持久批次，共 1 张图片', { exact: true }).waitFor();
  assert(writes.at(-1).body.prompt === '本批只描述车辆', 'batch caption override is submitted with job');
  assert(!errors.length, `no browser runtime errors: ${errors.join('; ')}`);
  return { checks, writesIntercepted: writes.length, scope: 'All API calls mocked; no real config changes or model requests.' };
}
