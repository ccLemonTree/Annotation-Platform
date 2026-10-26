async (page) => {
  const results = [];
  const record = async (name, root = page) => {
    await page.waitForFunction(expected => parseInt(getComputedStyle(document.querySelector('.platform-main')).marginLeft) === expected, page.viewportSize().width < 992 ? 0 : 216);
    await root.locator('html').evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    const metrics = await root.locator('html').evaluate(el => ({
      width: el.clientWidth, scrollWidth: el.scrollWidth,
      primary: getComputedStyle(el).getPropertyValue('--ui-primary').trim(),
      background: getComputedStyle(el).getPropertyValue('--ui-background').trim(),
      dark: el.classList.contains('dark'),
      scheme: getComputedStyle(el).colorScheme,
    }));
    if (metrics.scrollWidth > metrics.width + 1) throw new Error(name + ': horizontal overflow ' + JSON.stringify(metrics));
    if (metrics.primary !== '#699cff' || metrics.background !== '#0b101b' || metrics.scheme !== 'dark') throw new Error(name + ': theme mismatch');
    results.push({ name, ...metrics });
  };
  await page.setViewportSize({ width: 1440, height: 1000 });
  for (const [route, title] of [['/', '工作概览'], ['/projects', '项目管理'], ['/assets?project_id=e251de70-5c16-46f4-9cd9-d90a2262539e', '项目图片'], ['/annotations', '标注与审核'], ['/annotate/01ba049a-bc58-4749-848e-c3ce35979269', '检测标注工作台']]) {
    await page.goto('http://localhost:3000' + route);
    await page.getByRole('heading', { name: title, exact: true }).waitFor();
    await page.locator('.ant-spin-spinning').waitFor({ state: 'hidden' }).catch(() => {});
    if (route.startsWith('/assets')) await page.getByText('test-img', { exact: true }).first().waitFor();
    if (route.startsWith('/annotate')) await page.getByRole('img', { name: '检测画框区域' }).waitFor();
    await record('desktop ' + title);
    await page.screenshot({ path: 'output/playwright/ui-' + (route.split('/')[1]?.split('?')[0] || 'dashboard') + '-desktop.png' });
    await page.setViewportSize({ width: 390, height: 844 });
    await record('mobile ' + title);
    await page.setViewportSize({ width: 1440, height: 1000 });
  }
  await page.goto('http://localhost:3000/smarteye');
  const frame = page.frameLocator('iframe[title="SmartEye 数据拉取"]');
  await frame.getByRole('navigation', { name: 'SmartEye 功能导航' }).waitFor();
  for (const label of ['检测查询', '台账查询', '台账下载', '智搜下载', 'AI 生图', '工具任务']) {
    await frame.getByRole('navigation', { name: 'SmartEye 功能导航' }).getByText(label, { exact: true }).click();
    await frame.locator('.smarteye-content h1').waitFor();
    await record('SmartEye desktop ' + label, frame);
    await page.screenshot({ path: 'output/playwright/ui-smarteye-' + label + '.png' });
    await page.setViewportSize({ width: 390, height: 844 });
    await record('SmartEye mobile ' + label, frame);
    await page.setViewportSize({ width: 1440, height: 1000 });
  }
  await frame.getByRole('combobox', { name: '数据拉取用途' }).click();
  await frame.getByText('直接用于标注', { exact: true }).click();
  await frame.getByRole('combobox', { name: '标注项目' }).click();
  await frame.getByText('test-img', { exact: true }).click();
  await frame.getByRole('button', { name: /任务记录/ }).click();
  await frame.getByRole('dialog', { name: 'SmartEye 任务记录' }).waitFor();
  await frame.getByRole('button', { name: '刷新任务记录' }).click();
  await frame.getByRole('button', { name: '关闭' }).click();
  await frame.getByRole('navigation', { name: 'SmartEye 功能导航' }).getByText('检测查询', { exact: true }).click();
  await frame.getByRole('button', { name: '选择查询平台' }).click();
  await frame.getByRole('textbox', { name: '搜索查询平台' }).fill('codex-no-match');
  await frame.getByText('无匹配平台').waitFor();
  await frame.getByRole('button', { name: '选择查询平台' }).click();
  await record('SmartEye destination and task drawer', frame);
  return results;
}
