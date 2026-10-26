async (page) => {
  const tasks = [
    { task_id: 'ui-fixture-running', kind: 'detection', status: 'running', message: '界面验收运行任务' },
    { task_id: 'ui-fixture-failed', kind: 'ledger', status: 'failed', message: '界面验收失败任务' },
  ];
  const calls = [];
  let fail = false;
  const pattern = '**/api/smarteye/tasks**';
  await page.route(pattern, async route => {
    const request = route.request();
    if (request.method() === 'GET') {
      await route.fulfill({ json: { code: 0, data: tasks } }); return;
    }
    const operation = request.url().split('/').pop();
    const id = request.url().split('/').at(-2);
    const headers = await request.allHeaders();
    calls.push({ id, operation, authenticated: headers.authorization?.startsWith('Bearer ') || false,
      destinationHeader: !!headers['x-smarteye-purpose'] });
    if (fail) { await route.fulfill({ status: 409, json: { code: 409, message: '界面验收：旧任务版本，无法重试' } }); return; }
    tasks.find(t => t.task_id === id).status = operation === 'cancel' ? 'cancelled' : 'running';
    await route.fulfill({ json: { code: 0 } });
  });
  try {
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto('http://localhost:3000/smarteye');
    const frame = page.frameLocator('iframe[title="SmartEye 数据拉取"]');
    await frame.getByRole('button', { name: /任务记录/ }).click();
    const drawer = frame.getByRole('dialog', { name: 'SmartEye 任务记录' });
    await drawer.getByText('界面验收运行任务', { exact: true }).waitFor();
    await drawer.getByRole('button', { name: '取消任务', exact: true }).click();
    await drawer.getByText('已取消', { exact: true }).waitFor();
    await drawer.getByRole('row').filter({ hasText: '界面验收失败任务' }).getByRole('button', { name: '重试任务', exact: true }).click();
    await drawer.getByRole('row').filter({ hasText: '界面验收失败任务' }).getByText('运行中', { exact: true }).waitFor();
    fail = true;
    await drawer.getByRole('row').filter({ hasText: '界面验收运行任务' }).getByRole('button', { name: '重试任务', exact: true }).click();
    await drawer.getByRole('alert').getByText('界面验收：旧任务版本，无法重试', { exact: true }).waitFor();
    if (calls.length !== 3 || !calls.every(c => c.authenticated && !c.destinationHeader)) throw new Error('Task operation request contract mismatch');
    return { scope: 'browser fixtures only; no real tasks modified', checks: ['cancel state refresh', 'retry state refresh', 'operation error inline', 'authentication retained', 'operation destination headers absent'], calls };
  } finally {
    await page.unroute(pattern);
    await page.goto('http://localhost:3000/');
  }
}
