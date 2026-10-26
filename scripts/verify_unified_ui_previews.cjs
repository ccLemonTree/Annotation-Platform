async (page) => {
  const image = 'data:image/svg+xml,' + encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="64" height="48"><rect width="64" height="48" fill="#2563eb"/></svg>');
  const platformRoute = '**/api/smarteye/platforms';
  const ledgerRoute = '**/api/smarteye/ledger/device/log';
  let queries = 0;
  await page.route(platformRoute, async route => {
    if (route.request().method() !== 'POST') { await route.continue(); return; }
    queries++;
    await route.fulfill({ json: { code: 0, data: { records: [{ id: 'ui-preview', platform: 'UI验收平台', dataType: 'capture', targetType: '人', device: 'UI设备', deviceId: 'UI-CAM', timestamp: '2026-10-13 12:00:00', picUrl: image }], total: 1, pages: 1 } } });
  });
  await page.route(ledgerRoute, route => route.fulfill({ json: { code: 0, data: { records: [{ id: 'ui-image', backPath: image, deviceId: 'UI-CAM', createTime: '2026-10-13 12:00:00' }] } } }));
  try {
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto('http://localhost:3000/smarteye');
    const frame = page.frameLocator('iframe[title="SmartEye 数据拉取"]');
    await frame.getByRole('button', { name: '选择查询平台', exact: true }).click();
    await frame.getByRole('checkbox').first().check();
    await frame.getByRole('heading', { name: '目标检测结果查询', exact: true }).click();
    await frame.getByRole('button', { name: '查询', exact: true }).click();
    await frame.getByRole('button', { name: /查看详情/ }).click();
    const detail = frame.getByRole('dialog', { name: '图片详情', exact: true });
    await detail.getByRole('img', { name: '人 - UI设备', exact: true }).waitFor();
    await detail.getByRole('button', { name: '关闭', exact: true }).click();
    await frame.getByRole('navigation', { name: 'SmartEye 功能导航' }).getByText('台账查询', { exact: true }).click();
    await frame.getByRole('combobox', { name: '输入或选择平台', exact: true }).fill('UI验收平台');
    await frame.getByRole('heading', { name: '台账图片查询', exact: true }).click();
    await frame.getByRole('button', { name: '查询', exact: true }).click();
    await frame.getByRole('button', { name: '预览台账图片', exact: true }).click();
    const preview = frame.getByRole('dialog', { name: '图片预览', exact: true });
    await preview.getByRole('img', { name: '原图预览', exact: true }).waitFor();
    if (!await preview.getByRole('img', { name: '原图预览', exact: true }).evaluate(el => el.naturalWidth === 64)) throw new Error('Image preview failed');
    await preview.getByRole('button', { name: '关闭', exact: true }).focus();
    await page.keyboard.press('Escape');
    await preview.waitFor({ state: 'hidden' });
    if (queries !== 1) throw new Error('Query fixture not used');
    return { checks: ['detection details modal', 'shared original preview decoded', 'preview Escape closes'], scope: 'browser query fixtures only; no upstream calls' };
  } finally {
    await page.unroute(platformRoute);
    await page.unroute(ledgerRoute);
    await page.goto('http://localhost:3000/');
  }
}
