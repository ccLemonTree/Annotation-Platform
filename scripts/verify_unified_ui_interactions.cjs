async (page) => {
  const checks = [];
  const ensure = (condition, name) => { if (!condition) throw new Error(name); checks.push(name); };
  const modalCheck = async (root, title, field) => {
    const dialog = root.getByRole('dialog', { name: title, exact: true });
    await dialog.waitFor();
    if (field) { await dialog.getByRole('textbox', { name: field }).click(); await dialog.getByRole('textbox', { name: field }).fill('UI验收，不提交'); }
    ensure(await dialog.isVisible(), title + ' retains input');
    const scope = root === page ? page : page.frames().find(f => f.url().includes('/smarteye/index.html'));
    for (let i = 0; i < 12; i++) {
      await page.keyboard.press('Tab');
      if (!await scope.evaluate(() => document.activeElement === document.body || !!document.activeElement.closest('[role="dialog"], .ant-modal-wrap, .ant-drawer-content-wrapper'))) throw new Error(title + ' focus escaped to a background control');
    }
    checks.push(title + ' keyboard tab cycle');
    await dialog.getByRole('button', { name: '关闭', exact: true }).focus();
    await page.keyboard.press('Escape');
    await dialog.waitFor({ state: 'hidden' });
    checks.push(title + ' Escape closes');
  };
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto('http://localhost:3000/projects');
  await page.getByRole('button', { name: /新建项目/ }).click();
  await modalCheck(page, '新建项目', /项目名称/);
  await page.goto('http://localhost:3000/assets?project_id=e251de70-5c16-46f4-9cd9-d90a2262539e');
  await page.getByText('test-img', { exact: true }).first().waitFor();
  await page.goto('http://localhost:3000/assets');
  await page.locator('.page-toolbar-actions').getByRole('button', { name: /上传图片/ }).click();
  await modalCheck(page, '上传到图片库');
  await page.goto('http://localhost:3000/projects/e251de70-5c16-46f4-9cd9-d90a2262539e/images');
  await page.getByRole('button', { name: '导出 / 冻结数据集' }).click();
  await modalCheck(page, '检测数据导出');
  await page.goto('http://localhost:3000/detection-query');
  const frame = page.frameLocator('iframe[title="SmartEye 数据拉取"]');
  const nav = frame.getByRole('navigation', { name: 'SmartEye 功能导航' });
  await nav.waitFor();
  checks.push('legacy detection-query route preserved');
  await nav.getByText('台账下载', { exact: true }).click();
  await frame.getByRole('button', { name: /新建下载任务/ }).click();
  await modalCheck(frame, '新建下载任务', /名称/);
  await frame.getByRole('button', { name: /更新设备/ }).click();
  await modalCheck(frame, '更新设备');
  await frame.getByRole('button', { name: '监听下载', exact: true }).click();
  await frame.getByRole('button', { name: /新建监听任务/ }).click();
  await modalCheck(frame, '新建监听任务', /名称/);
  await nav.getByText('AI 生图', { exact: true }).click();
  await frame.getByRole('button', { name: /查看历史/ }).click();
  await modalCheck(frame, 'AI 生图历史', '历史提示词');
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForFunction(() => parseInt(getComputedStyle(document.querySelector('.platform-main')).marginLeft) === 0);
  await frame.locator('html').evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  await frame.getByRole('menuitem', { name: '更多功能', exact: true }).click();
  await frame.locator('.ant-menu-submenu-popup:visible').getByText('工具任务', { exact: true }).click();
  await frame.getByRole('heading', { name: '执行工具任务', exact: true }).waitFor();
  checks.push('mobile overflow navigation remains accessible');
  await frame.getByRole('button', { name: /任务记录/ }).click();
  await frame.getByRole('dialog', { name: 'SmartEye 任务记录' }).waitFor();
  ensure(await frame.locator('html').evaluate(el => el.scrollWidth === el.clientWidth), 'mobile task drawer has no page overflow');
  await frame.getByRole('button', { name: '关闭', exact: true }).click();
  await page.goto('http://localhost:3000/login');
  await page.getByRole('textbox', { name: /用户名/ }).waitFor();
  ensure(await page.locator('html').evaluate(el => el.scrollWidth === el.clientWidth), 'mobile login no overflow');
  await page.getByRole('button', { name: '登 录', exact: true }).click();
  await page.getByText('请输入用户名', { exact: true }).waitFor();
  checks.push('login field validation inline');
  await page.getByRole('tab', { name: '注册', exact: true }).click();
  await page.getByRole('textbox', { name: '邮箱（可选）', exact: true }).waitFor();
  checks.push('registration labels retained');
  return { count: checks.length, checks };
}
