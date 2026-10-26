async page => {
  await page.unrouteAll(); page.setDefaultTimeout(8000);
  const origin = 'http://127.0.0.1:3000';
  const source = await (await page.request.get(origin + '/src/components/ProjectExport.tsx')).text();
  const react = source.match(/["'](\/node_modules\/\.vite\/deps\/react\.js\?[^"']+)["']/)[1];
  const asset = { id: 'a', original_name: 'a.png' };
  let release, releaseSummary, oldRequests = 0, summaryRequests = 0, downloads = 0;
  let snapshots = [{ id: 'existing', format: 'yolo', asset_count: 7, created_at: new Date().toISOString() }];
  const held = new Promise(resolve => release = resolve);
  const summaryHeld = new Promise(resolve => releaseSummary = resolve);
  await page.route('**/src/App.tsx*', route => route.fulfill({ contentType: 'application/javascript', body: `import React from '${react}';import Provider from '/src/ui/PlatformProvider.tsx';import Export from '/src/components/ProjectExport.tsx';export default function App(){return React.createElement(Provider,null,React.createElement(Export,{projectId:'fixture'}));}` }));
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (!path.startsWith('/api/')) return route.continue();
    if (path.endsWith('/summary')) {
      summaryRequests++;
      await summaryHeld;
      return route.fulfill({ json: { labels: ['fire'], all: { images: 3, images_by_label: { fire: 2 } }, approved: { images: 1, images_by_label: { fire: 1 } } } });
    }
    if (path === '/api/assets' || path === '/api/annotations') {
      oldRequests++;
      return route.fulfill({ json: { items: path === '/api/assets' ? [asset] : [{ asset_id: 'a', review_status: 'approved', annotation_data: { boxes: [{ label: 'fire' }] } }], total: 1 } });
    }
    if (path.endsWith('/snapshots')) {
      if (route.request().method() === 'POST') {
        if (route.request().postDataJSON().selection.mode !== 'val_only') throw Error('Freeze lost validation-only mode');
        await held;
        snapshots = [{ id: 'frozen', format: 'yolo', asset_count: 1, created_at: new Date().toISOString() }, ...snapshots];
        return route.fulfill({ json: snapshots[0] });
      }
      return route.fulfill({ json: snapshots });
    }
    if (path.endsWith('/splits/preview')) {
      if (route.request().postDataJSON().selection.mode !== 'val_only') throw Error('Preview lost validation-only mode');
      return route.fulfill({ json: { images_by_split: { val: 1 }, labels_by_split: { val: { fire: 1 } }, excluded_asset_ids: [] } });
    }
    if (path.endsWith('/download')) { downloads++; return route.fulfill({ contentType: 'application/zip', body: 'fixture zip' }); }
    if (route.request().method() === 'DELETE') {
      if (path.endsWith('/existing')) return route.fulfill({ status: 409, json: { detail: '冻结版本已被训练任务或验证集引用' } });
      if (path.endsWith('/frozen')) { snapshots = snapshots.filter(row => row.id !== 'frozen'); return route.fulfill({ status: 204 }); }
    }
    throw Error('Unexpected API: ' + path);
  });
  try {
    await page.goto(origin);
    await page.getByRole('button', { name: '导出 / 冻结数据集', exact: true }).click();
    await page.getByRole('button', { name: '下载 yolo · 7 张', exact: true }).waitFor();
    await page.getByText('正在加载标签…', { exact: true }).waitFor();
    const downloaded = page.waitForEvent('download');
    await page.getByRole('button', { name: '下载 yolo · 7 张', exact: true }).click();
    await downloaded;
    releaseSummary();
    await page.getByRole('combobox', { name: '划分方式' }).click();
    await page.getByText('仅验证集', { exact: true }).click();
    await page.getByRole('button', { name: '预览当前划分', exact: true }).click();
    const freeze = page.getByRole('button', { name: '仅冻结', exact: true });
    await freeze.click();
    await page.getByText(/正在处理，已等待 [1-9]\d* 秒/).waitFor({ timeout: 4500 });
    if (!await freeze.isDisabled()) throw Error('Freeze allows duplicate requests');
    release();
    await page.getByRole('button', { name: '下载 yolo · 1 张', exact: true }).waitFor();
    await page.getByText(/正在处理，已等待/).waitFor({ state: 'hidden' });
    await page.getByLabel('仅审核通过').uncheck();
    await page.getByText('当前范围 3 张', { exact: false }).waitFor();
    if (summaryRequests !== 1 || oldRequests !== 0) throw Error(`Expected one summary, got summary=${summaryRequests}, paginated=${oldRequests}`);
    if (downloads !== 1) throw Error('Freeze-only triggered a download');
    const existing = page.getByRole('button', { name: '下载 yolo · 7 张', exact: true });
    await page.getByRole('button', { name: '删除冻结版本 existing', exact: true }).click();
    await page.getByRole('button', { name: /^删\s*除$/ }).click();
    await page.getByText('冻结版本已被训练任务或验证集引用', { exact: true }).waitFor();
    if (!await existing.isVisible()) throw Error('Referenced snapshot disappeared');
    const frozen = page.getByRole('button', { name: '下载 yolo · 1 张', exact: true });
    await page.getByRole('button', { name: '删除冻结版本 frozen', exact: true }).click();
    await page.getByRole('button', { name: /^删\s*除$/ }).click();
    await frozen.waitFor({ state: 'hidden' });
    return { checks: ['Summary loads labels without paginated asset or annotation requests', 'Review filter uses matching counts', 'Freeze elapsed seconds advance and clear on completion', 'Freeze cannot be submitted twice', 'Existing snapshot shows and downloads before labels load', 'Validation-only mode reaches preview and freeze', 'Referenced snapshot delete stays blocked', 'Confirmed deletion removes the row', 'Freeze-only does not download'], businessWrites: 0 };
  } finally {
    release(); releaseSummary(); await page.goto('about:blank'); await page.unrouteAll({ behavior: 'ignoreErrors' });
  }
}
