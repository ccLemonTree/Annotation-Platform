// Run with: playwright-cli -s=image-semantics run-code --filename scripts/verify_image_semantics_ui.cjs
// Every API request is intercepted; no business writes or model calls.
async (page) => {
  page.setDefaultTimeout(12000);
  await page.setViewportSize({ width: 1500, height: 1000 });
  const source = await (await page.request.get('http://127.0.0.1:3000/src/pages/ImageSemanticsPage.tsx')).text();
  const react = source.match(/["'](\/node_modules\/\.vite\/deps\/react\.js\?[^"']+)["']/)?.[1];
  if (!react) throw new Error('Vite React import not found');
  const image = '11111111-1111-4111-8111-111111111111', group = '22222222-2222-4222-8222-222222222222';
  const caption = '33333333-3333-4333-8333-333333333333', embedding = '44444444-4444-4444-8444-444444444444';
  const jobId = '55555555-5555-4555-8555-555555555555', itemId = '66666666-6666-4666-8666-666666666666';
  const fixture = { id: image, original_name: '隔离测试图片.png', width: 64, height: 48, collection_names: ['测试图片集'] };
  let jobs = [], item = { id: itemId, image_id: image, original_name: fixture.original_name, status: 'pending', attempt: 0, caption: null, error: null };
  let overLimit = false, lateRelease, lateStarted = false, delaySearch = false, lateDone;
  const lateFinished = new Promise(resolve => { lateDone = resolve; });
  const writes = [], checks = [], errors = [];
  const onError = error => errors.push(error.message);
  page.on('pageerror', onError);
  const assert = (value, message) => { if (!value) throw new Error(message); checks.push(message); };
  const counts = () => ({ [item.status]: 1 });
  const job = () => ({ id: jobId, collection_id: null, caption_model_id: caption, embedding_model_id: embedding, dimensions: 2048, preprocessing: 'test', created_at: '2026-10-15T12:00:00', total: 1, counts: counts() });
  const api = async route => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname, method = request.method();
    if (!path.startsWith('/api/')) return route.continue();
    const json = (data, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) });
    if (method !== 'GET') writes.push({ path, body: request.postDataJSON() });
    if (path === '/api/images/collections') return json([{ id: group, name: '测试图片集', image_count: 1 }]);
    if (path === '/api/model-services') return json([{ id: caption, name: 'Qwen描述', kind: 'caption', enabled: true, revision: 1 }, { id: embedding, name: 'WEMM向量', kind: 'embedding', enabled: true, revision: 1 }]);
    if (path === '/api/images') return json({ items: [fixture], total: 1, page: 1, page_size: 20 });
    if (path.endsWith('/content')) return route.fulfill({ contentType: 'image/png', body: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl6uSAAAAAASUVORK5CYII=', 'base64') });
    if (path === '/api/image-semantics/jobs') {
      if (method === 'POST') { jobs = [job()]; return json(job(), 201); }
      return json(jobs.length ? [job()] : []);
    }
    if (path.endsWith('/cancel') || path.endsWith('/retry')) {
      item = { ...item, status: path.endsWith('/cancel') ? 'cancelled' : 'pending', attempt: item.attempt + 1 };
      return json({ changed_count: 1 });
    }
    if (path === `/api/image-semantics/jobs/${jobId}`) return json({ ...job(), items: [item], page: 1, page_size: 20 });
    if (path === '/api/image-semantics/search') {
      if (overLimit) return json({ detail: '当前索引超过5000条，请缩小图片集；未截断结果' }, 409);
      const query = request.postDataJSON();
      const wasDelayed = delaySearch;
      if (wasDelayed) { lateStarted = true; await new Promise(resolve => { lateRelease = resolve; }); }
      const response = await json({ items: [{ image_id: image, original_name: wasDelayed ? '过期检索结果' : fixture.original_name, caption: '红色图片', score: 0.95 }], indexed_count: 1, limit: 5000, space: { model_id: embedding, revision: 1, dimensions: 2048, preprocessing: 'test' }, warning: '相似度仅用于检索，不代表标注正确性', query });
      if (wasDelayed) lateDone();
      return response;
    }
    throw new Error(`unexpected API ${method} ${path}`);
  };
  const harness = route => route.fulfill({ contentType: 'application/javascript', body: `import React from '${react}'; import Provider from '/src/ui/PlatformProvider.tsx'; import Page from '/src/pages/ImageSemanticsPage.tsx'; export default function App() { return React.createElement(Provider,null,React.createElement(Page)); }` });
  await page.route('**/api/**', api);
  await page.route('**/src/App.tsx*', harness);
  try {
    await page.goto('http://127.0.0.1:3000/image-semantics');
    await page.getByText('图片描述与向量检索', { exact: true }).waitFor();
    assert(await page.getByText(/相似度仅用于检索/).count() > 0, 'scale and similarity caveat visible');
    const select = async (name, option) => { await page.getByRole('combobox', { name, exact: true }).click(); await page.locator('.ant-select-dropdown:visible').getByText(option, { exact: true }).click(); };
    await select('描述模型', 'Qwen描述 · r1'); await select('向量模型', 'WEMM向量 · r1');
    await page.getByRole('row', { name: /隔离测试图片.png/ }).getByRole('checkbox').check();
    await page.getByRole('button', { name: '生成所选 1 张', exact: true }).click();
    await page.getByRole('button', { name: '取消此项', exact: true }).waitFor();
    assert(writes[0].body.image_ids[0] === image && writes[0].body.embedding_model_id === embedding, 'selected images and model IDs submitted');
    await page.getByRole('button', { name: '取消此项', exact: true }).click();
    await page.getByText('已取消', { exact: true }).first().waitFor();
    assert(writes.at(-1).body.item_ids[0] === itemId, 'per-item cancellation submitted');
    await page.getByRole('button', { name: '重试此项', exact: true }).click();
    assert(writes.at(-1).path.endsWith('/retry') && writes.at(-1).body.item_ids[0] === itemId, 'per-item retry submitted');
    await page.getByRole('button', { name: '取消未完成项', exact: true }).click();
    assert(!writes.at(-1).body.item_ids, 'batch cancellation submitted');
    await page.getByRole('button', { name: '重试失败与取消项', exact: true }).click();
    await page.getByRole('textbox', { name: '检索文本', exact: true }).fill('红色场景');
    await page.getByRole('button', { name: /检\s*索/, exact: true }).click();
    await page.getByText('0.9500', { exact: true }).waitFor();
    assert(writes.at(-1).body.text === '红色场景', 'text search and cosine result rendered');
    await page.getByText('以图搜图', { exact: true }).click();
    await select('查询图片', fixture.original_name);
    await page.getByRole('button', { name: /检\s*索/, exact: true }).click();
    await page.getByText('0.9500', { exact: true }).waitFor();
    assert(writes.at(-1).body.image_id === image && !writes.at(-1).body.text, 'image search submitted with exclusive source');
    await page.getByText('文本检索', { exact: true }).click();
    delaySearch = true;
    await page.getByRole('button', { name: /检\s*索/, exact: true }).click();
    await page.waitForFunction(() => true);
    while (!lateStarted) await new Promise(resolve => setTimeout(resolve, 20));
    await page.getByRole('textbox', { name: '检索文本', exact: true }).fill('修改后的条件');
    lateRelease(); delaySearch = false;
    await lateFinished;
    await page.getByRole('button', { name: /检\s*索/, exact: true }).waitFor();
    assert(await page.getByText('过期检索结果', { exact: true }).count() === 0, 'late result discarded after input changes');
    overLimit = true;
    await page.getByRole('button', { name: /检\s*索/, exact: true }).click();
    await page.getByText('当前索引超过5000条，请缩小图片集；未截断结果', { exact: true }).first().waitFor();
    assert(true, 'over-limit backend error visible');
    await select('图片集筛选', '测试图片集 (1)');
    await page.getByRole('button', { name: '生成当前图片集', exact: true }).click();
    assert(writes.at(-1).body.collection_id === group && !writes.at(-1).body.image_ids, 'whole collection job submitted without truncating selection');
    await page.screenshot({ path: 'output/playwright/image-semantics.png', fullPage: true });
    assert(errors.length === 0, `no browser errors: ${errors.join(';')}`);
    return { checks, interceptedWrites: writes.length, realBusinessWrites: 0, screenshot: 'output/playwright/image-semantics.png' };
  } finally {
    lateRelease?.(); page.off('pageerror', onError);
    await page.unroute('**/api/**', api); await page.unroute('**/src/App.tsx*', harness);
  }
}
