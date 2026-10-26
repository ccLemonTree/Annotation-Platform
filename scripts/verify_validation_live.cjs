async (page) => {
  const errors = [], responses = [];
  let writes = 0;
  const failed = error => errors.push(error.message);
  const response = result => {
    const path = new URL(result.url()).pathname;
    if (path.startsWith('/api/validation/') || path.endsWith('/snapshots')) responses.push({ path, status: result.status() });
  };
  const guard = route => {
    if (!['GET', 'HEAD', 'OPTIONS'].includes(route.request().method())) { writes++; return route.abort(); }
    return route.continue();
  };
  page.on('pageerror', failed); page.on('response', response);
  await page.route('**/api/**', guard);
  try {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto('http://localhost:3000/validation');
    await page.getByRole('heading', { name: '验证集', exact: true, level: 1 }).waitFor();
    await page.getByRole('button', { name: '创建验证集', exact: true }).waitFor();
    await page.waitForFunction(() => !document.querySelector('.ant-spin-spinning'));
    await page.getByRole('menuitem', { name: '验证集', exact: true }).waitFor();
    await page.getByRole('button', { name: '创建验证集', exact: true }).click();
    const modal = page.getByRole('dialog');
    await modal.getByText('需先完成人工审核', { exact: true }).waitFor();
    await modal.getByRole('combobox', { name: '验证项目', exact: true }).click();
    await page.locator('.ant-select-dropdown:visible .ant-select-item-option').first().click();
    await modal.getByRole('combobox', { name: '真值来源', exact: true }).click();
    await page.locator('.ant-select-dropdown:visible').getByText('冻结包 val/test 划分', { exact: true }).click();
    await modal.getByRole('combobox', { name: '冻结包', exact: true }).waitFor();
    await page.waitForFunction(() => !document.querySelector('.ant-select-loading'));
    await modal.getByRole('button', { name: /^取\s*消$/ }).click();
    await modal.waitFor({ state: 'hidden' });
    await page.screenshot({ path: 'output/playwright/validation-live.png', fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.waitForFunction(() => document.documentElement.scrollWidth <= innerWidth + 1);
    await page.screenshot({ path: 'output/playwright/validation-live-narrow.png', fullPage: true });
    if (writes || errors.length || responses.some(row => row.status >= 400)) throw Error(JSON.stringify({ writes, errors, responses }));
    if (!responses.some(row => row.path === '/api/validation/sets') || !responses.some(row => row.path === '/api/validation/runs') || !responses.some(row => row.path.endsWith('/snapshots'))) throw Error('缺少真实验证接口读取');
    return { checks: ['真实侧栏与页面', '验证集及任务认证读取', '创建表单及真实冻结来源读取（未提交）', '390px无页面横向溢出'], businessWrites: writes, errors, responses };
  } finally {
    page.off('pageerror', failed); page.off('response', response);
    await page.unroute('**/api/**', guard);
    await page.setViewportSize({ width: 1440, height: 900 });
  }
}
