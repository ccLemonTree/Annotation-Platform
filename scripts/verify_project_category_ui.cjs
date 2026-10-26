async (page) => {
  const id = 'e251de70-5c16-46f4-9cd9-d90a2262539e';
  const checks = []; let changed;
  const project = { id, project_name: 'test-img', task_type: 'detection', status: 'active', description: '',
    label_schema: { labels: ['人'], retained_metadata: 'preserve' }, created_at: '2026-10-13T00:00:00Z', total_assets: 6 };
  const list = 'http://localhost:3000/api/projects?**', detail = 'http://localhost:3000/api/projects/' + id;
  await page.route(list, route => route.fulfill({ json: { items: [project], total: 1, page: 1, page_size: 10 } }));
  await page.route(detail, async route => {
    if (route.request().method() === 'PUT') { changed = route.request().postDataJSON(); Object.assign(project, changed); }
    await route.fulfill({ json: project });
  });
  try {
    await page.goto('http://localhost:3000/projects');
    await page.getByRole('button', { name: /编辑/ }).click();
    const dialog = page.getByRole('dialog', { name: '编辑项目', exact: true });
    const labels = dialog.getByRole('combobox', { name: '项目标注类别', exact: true });
    await labels.fill('车'); await labels.press('Enter');
    await dialog.getByRole('button', { name: '确 定', exact: true }).click();
    await dialog.waitFor({ state: 'hidden' });
    if (JSON.stringify(changed.label_schema.labels) !== JSON.stringify(['人', '车']) || changed.label_schema.retained_metadata !== 'preserve') throw new Error('label config request loses metadata');
    checks.push('project edit exposes labels and preserves schema metadata');
    // Unconfigured legacy project must not silently become a person-detection project.
    project.label_schema = { labels: [] };
    await page.goto('http://localhost:3000/annotate/01ba049a-bc58-4749-848e-c3ce35979269');
    await page.getByText('此项目尚未配置标注类别，先到项目管理中编辑“标注类别”。', { exact: true }).waitFor();
    if (!await page.getByRole('button', { name: '保存标注', exact: true }).isDisabled()) throw new Error('Unconfigured project can save');
    checks.push('unconfigured project prompts configuration instead of defaulting to person');
    return { checks, scope: 'project writes intercepted; no real project settings changed' };
  } finally { await page.unroute(list); await page.unroute(detail); }
}
