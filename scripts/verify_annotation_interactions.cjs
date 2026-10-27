async (page) => {
  const assetId = '01ba049a-bc58-4749-848e-c3ce35979269';
  const projectId = 'e251de70-5c16-46f4-9cd9-d90a2262539e';
  const checks = [];
  const ensure = (value, name) => { if (!value) throw new Error(name); checks.push(name); };
  let current, savedPayload, conflict = false;
  const annotationPattern = 'http://localhost:3000/api/annotations**';
  const projectPattern = '**/api/projects/' + projectId;
  await page.route(projectPattern, async route => {
    const response = await route.fetch(); const project = await response.json();
    await route.fulfill({ json: { ...project, label_schema: { labels: ['车', '人'] } } });
  });
  await page.route(annotationPattern, async route => {
    const request = route.request(), url = new URL(request.url());
    if (url.pathname.endsWith('/events')) { await route.fulfill({ json: [] }); return; }
    if (request.method() === 'GET') {
      await route.fulfill({ json: { items: current ? [current] : [], total: current ? 1 : 0, page: 1, page_size: 100 } }); return;
    }
    savedPayload = request.postDataJSON();
    if (conflict) { await route.fulfill({ status: 409, json: { detail: '标注已被修改，请重新加载最新版本' } }); return; }
    current = { id: '11111111-1111-4111-8111-111111111111', asset_id: assetId, annotation_data: savedPayload.annotation_data,
      is_latest: true, version: current ? current.version + 1 : 1, review_status: 'pending', annotation_source: 'manual' };
    await route.fulfill({ json: current });
  });
  try {
    await page.setViewportSize({ width: 1440, height: 1100 });
    await page.goto('http://localhost:3000/annotate/' + assetId);
    const canvas = page.getByRole('img', { name: '检测画框区域' });
    await canvas.waitFor();
    ensure(await page.getByRole('combobox', { name: '框类别', exact: true }).inputValue() === '', 'category select renders project options');
    await canvas.focus();
    const coordinates = async (x, y) => canvas.evaluate((svg, p) => {
      const point = svg.createSVGPoint(); point.x = p.x; point.y = p.y;
      const c = point.matrixTransform(svg.getScreenCTM()); return { x: c.x, y: c.y };
    }, { x, y });
    const draw = async (x, y, w, h) => {
      const start = await coordinates(x, y), end = await coordinates(x + w, y + h);
      await page.keyboard.down('Shift'); await page.mouse.move(start.x, start.y); await page.mouse.down();
      await page.mouse.move(end.x, end.y, { steps: 5 }); await page.mouse.up(); await page.keyboard.up('Shift');
    };
    const boxes = () => canvas.locator('g > rect:not([data-resize])').evaluateAll(elements => elements.map(el => ({
      x: +el.getAttribute('x'), y: +el.getAttribute('y'), width: +el.getAttribute('width'), height: +el.getAttribute('height'),
    })));
    await draw(100, 100, 400, 400);
    ensure((await boxes()).length === 1, 'draw rectangle');
    await canvas.focus(); await page.keyboard.press('Control+z');
    ensure((await boxes()).length === 0, 'undo drawing');
    await page.keyboard.press('Control+Shift+z');
    ensure((await boxes()).length === 1, 'redo drawing');
    const original = await boxes();
    await page.getByRole('button', { name: '放大图片', exact: true }).click();
    ensure(JSON.stringify(await boxes()) === JSON.stringify(original), 'zoom preserves pixel coordinates');
    await page.getByRole('button', { name: '适应图片', exact: true }).click();
    let cursor = await coordinates(300, 300); await page.mouse.move(cursor.x, cursor.y);
    await page.mouse.wheel(0, -100);
    await page.waitForFunction(() => document.querySelector('svg.workspace-canvas').getAttribute('viewBox').split(' ')[0] !== '0');
    await draw(180, 180, 100, 100);
    ensure((await boxes()).length === 2, 'Shift draws nested rectangle after zoom');
    let p = await coordinates(200, 200); await page.mouse.click(p.x, p.y);
    ensure(Math.abs(+(await page.getByRole('spinbutton', { name: '框width' }).inputValue()) - 100) < 1, 'overlap selects smaller rectangle');
    await page.keyboard.down('Alt'); await page.mouse.click(p.x, p.y); await page.keyboard.up('Alt');
    ensure(Math.abs(+(await page.getByRole('spinbutton', { name: '框width' }).inputValue()) - 400) < 1, 'Alt cycles overlapping rectangles');
    await canvas.focus(); await page.keyboard.press('Delete');
    ensure((await boxes()).length === 1, 'Delete removes selected rectangle');
    await page.keyboard.press('Control+z');
    ensure((await boxes()).length === 2, 'undo restores deleted rectangle');
    await draw(185, 185, 40, 40); await canvas.focus(); await page.keyboard.press('Escape');
    // Completed draws remain; Escape cancels only an active gesture.
    await page.keyboard.press('Control+z');
    ensure((await boxes()).length === 2, 'completed draw remains undoable after Escape');
    const beforeMove = await boxes();
    let startMove = await coordinates(350, 350), endMove = await coordinates(370, 370);
    await page.mouse.move(startMove.x, startMove.y); await page.mouse.down(); await page.mouse.move(endMove.x, endMove.y, { steps: 3 }); await page.mouse.up();
    ensure(Math.abs((await boxes())[0].x - beforeMove[0].x - 20) < 1, 'move uses image coordinates under zoom');
    await canvas.focus(); await page.keyboard.press('Control+z');
    ensure(JSON.stringify(await boxes()) === JSON.stringify(beforeMove), 'undo restores moved box');
    await canvas.focus(); await page.keyboard.press('Shift+Tab');
    ensure(Math.abs(+(await page.getByRole('spinbutton', { name: '框width' }).inputValue()) - 100) < 1, 'Shift Tab starts from last box');
    await page.keyboard.press('Shift+Tab');
    const beforeResize = await boxes();
    const handle = canvas.locator('rect[data-resize="se"]');
    const bounds = await handle.boundingBox();
    await page.mouse.move(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2); await page.mouse.down();
    await page.mouse.move(bounds.x + bounds.width / 2 + 15, bounds.y + bounds.height / 2 + 15, { steps: 3 }); await page.mouse.up();
    ensure(JSON.stringify(await boxes()) !== JSON.stringify(beforeResize), 'resize handle changes geometry');
    await canvas.focus(); await page.keyboard.press('Control+z');
    ensure(JSON.stringify(await boxes()) === JSON.stringify(beforeResize), 'undo restores resized box');
    const cancelStart = await coordinates(320, 320), cancelEnd = await coordinates(380, 380);
    await page.keyboard.down('Shift'); await page.mouse.move(cancelStart.x, cancelStart.y); await page.mouse.down();
    await page.mouse.move(cancelEnd.x, cancelEnd.y, { steps: 3 }); await page.keyboard.press('Escape'); await page.mouse.up(); await page.keyboard.up('Shift');
    ensure((await boxes()).length === 2, 'Escape cancels active drawing without adding undo step');
    const input = page.getByRole('spinbutton', { name: '框x', exact: true });
    await canvas.focus(); await page.keyboard.press('Tab'); await input.focus(); await page.keyboard.press('Delete');
    ensure((await boxes()).length === 2, 'Delete in numeric input does not remove box');
    await canvas.focus(); await page.keyboard.press('2');
    ensure((await canvas.locator('g > text').first().textContent()).includes('人'), 'number shortcut reassigns selected box category');
    await page.keyboard.press('Control+z');
    await draw(600, 100, 100, 100);
    ensure((await boxes()).length === 3, 'number shortcut selects a different configured category');
    await canvas.focus(); await page.keyboard.press('Control+s');
    await page.getByText('标注已保存，等待审核', { exact: true }).waitFor();
    ensure(savedPayload.annotation_data.boxes.at(-1).label === '人' && savedPayload.annotation_data.boxes[0].label === '车', 'save uses project categories and original pixels');
    ensure(await page.getByRole('button', { name: '撤销标注操作', exact: true }).isDisabled(), 'successful save resets local undo history');
    await draw(750, 100, 100, 100); conflict = true;
    await canvas.focus(); await page.keyboard.press('Control+s');
    await page.getByText('保存失败；若版本冲突，请先记录当前修改，再重新加载最新版本', { exact: true }).waitFor();
    ensure((await boxes()).length === 4, '409 preserves unsaved local rectangles');
    ensure(savedPayload.base_version === 1, 'save retains base-version concurrency check');
    await canvas.focus(); await page.keyboard.press('Control+z');
    const view = await canvas.getAttribute('viewBox');
    p = await coordinates(500, 350);
    await page.keyboard.down('Control'); await page.mouse.move(p.x, p.y); await page.mouse.down();
    await page.mouse.move(p.x + 60, p.y + 30, { steps: 5 }); await page.mouse.up(); await page.keyboard.up('Control');
    ensure(await canvas.getAttribute('viewBox') !== view && (await boxes()).length === 3, 'Ctrl pan changes view only');
    await page.getByRole('button', { name: '适应图片', exact: true }).click();
    await page.screenshot({ path: 'output/playwright/annotation-interactions.png' });
    return { checks, scope: 'real image read; project labels and annotation writes intercepted in browser; no real annotations changed' };
  } finally {
    await page.unroute(annotationPattern); await page.unroute(projectPattern);
  }
}
