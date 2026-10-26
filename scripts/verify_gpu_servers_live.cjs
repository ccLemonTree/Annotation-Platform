async (page) => {
  const errors = [], checks = [];
  let writes = 0;
  const failed = error => errors.push(error.message);
  const consoleFailed = message => { if (message.type() === 'error') errors.push(message.text()); };
  const guard = route => {
    if (!['GET', 'HEAD', 'OPTIONS'].includes(route.request().method())) { writes++; return route.abort(); }
    return route.continue();
  };
  page.on('pageerror', failed); page.on('console', consoleFailed); await page.route('**/api/**', guard);
  try {
    await page.setViewportSize({ width: 1440, height: 900 });
    const listResponse = page.waitForResponse(response => new URL(response.url()).pathname === '/api/gpu-resources/servers');
    const gpuResponse = page.waitForResponse(response => new URL(response.url()).pathname === '/api/gpu-resources');
    await page.goto('http://localhost:3000/gpu-resources');
    const listed = await listResponse, collected = await gpuResponse;
    if (listed.status() !== 200 || collected.status() !== 200) throw Error('真实GPU接口读取失败');
    const servers = await listed.json(), snapshot = await collected.json();
    if (!servers.some(row => row.id === 'default') || servers.some(row => 'password' in row || 'password_encrypted' in row)) throw Error('连接摘要契约不符');
    if (!snapshot.gpus.length) throw Error('真实NVIDIA采集没有返回显卡');
    await page.getByText('资源概览', { exact: true }).waitFor();
    for (const gpu of snapshot.gpus) await page.getByRole('cell').filter({ hasText: gpu.uuid }).first().waitFor();
    await page.getByRole('switch', { name: '自动30秒刷新', exact: true }).uncheck();
    checks.push('真实连接摘要与原NVIDIA服务器采集');
    await page.screenshot({ path: 'output/playwright/gpu-servers-live.png', fullPage: true });
    await page.getByRole('button', { name: '新增服务器', exact: true }).click();
    const modal = page.getByRole('dialog', { name: '新增GPU服务器', exact: true });
    if (await modal.getByLabel('用户名', { exact: true }).inputValue() !== 'root') throw Error('默认用户名不符');
    if (await modal.getByLabel('SSH端口', { exact: true }).inputValue() !== '22') throw Error('默认端口不符');
    if (await modal.locator('input[type=password]').inputValue()) throw Error('新连接密码应为空');
    await modal.getByRole('button', { name: '保存连接', exact: true }).waitFor();
    checks.push('真实新增表单默认值与密码空白（未提交）');
    await page.screenshot({ path: 'output/playwright/gpu-servers-form-live.png', fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.waitForFunction(() => document.documentElement.scrollWidth <= innerWidth + 1);
    await page.screenshot({ path: 'output/playwright/gpu-servers-form-live-narrow.png' });
    await modal.getByRole('button', { name: /^取\s*消$/ }).click();
    await modal.waitFor({ state: 'hidden' });
    await page.waitForFunction(() => document.documentElement.scrollWidth <= innerWidth + 1);
    checks.push('390px页面及表单无横向溢出');
    if (writes || errors.length) throw Error(JSON.stringify({ writes, errors }));
    return { checks, gpuCount: snapshot.gpus.length, processCount: snapshot.processes.length,
      processesComplete: snapshot.processes_complete, realBusinessWrites: writes, errors };
  } finally {
    page.off('pageerror', failed); page.off('console', consoleFailed); await page.unroute('**/api/**', guard);
    await page.setViewportSize({ width: 1440, height: 900 });
  }
}
