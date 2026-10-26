async page => {
  await page.unrouteAll(); page.setDefaultTimeout(8000);
  const origin = 'http://127.0.0.1:3000';
  const source = await (await page.request.get(origin + '/src/components/validation/ValidationForms.tsx')).text();
  const react = source.match(/["'](\/node_modules\/\.vite\/deps\/react\.js\?[^"']+)["']/)[1];
  let release, writes = 0;
  const held = new Promise(resolve => release = resolve);
  await page.route('**/src/App.tsx*', route => route.fulfill({ contentType: 'application/javascript', body: `import React from '${react}';import Provider from '/src/ui/PlatformProvider.tsx';import {CreateValidationSet} from '/src/components/validation/ValidationForms.tsx';export default function App(){const [saved,setSaved]=React.useState(false);return React.createElement(Provider,null,saved?'保存成功':React.createElement(CreateValidationSet,{projects:[{id:'fixture',project_name:'验证项目'}],projectId:'fixture',onClose:()=>{},onCreated:()=>setSaved(true)}));}` }));
  await page.route('**/api/**', async route => {
    const request = route.request(), path = new URL(request.url()).pathname;
    if (!path.startsWith('/api/')) return route.continue();
    if (path === '/api/validation/sets' && request.method() === 'POST') {
      writes++; await held;
      return route.fulfill({ status: 201, json: { id: 'created', name: '验证数据', total: 3945 } });
    }
    throw Error('Unexpected API ' + path);
  });
  try {
    await page.goto(origin);
    const timeoutMessage = await page.evaluate(async () => {
      const { validationError } = await import('/src/api/validation.ts');
      return validationError({ isAxiosError: true, code: 'ECONNABORTED' }, '验证集创建失败');
    });
    if (!timeoutMessage.includes('刷新列表')) throw Error('Timeout message encourages a blind retry');
    await page.evaluate(async () => {
      const client = (await import('/src/api/client.ts')).default;
      client.interceptors.request.use(config => { if (config.url === '/api/validation/sets') window.validationSaveTimeout = config.timeout; return config; });
    });
    await page.getByLabel('验证集名称').fill('验证数据');
    await page.getByRole('button', { name: '保存验证集', exact: true }).click();
    await page.getByText(/正在保存验证集，已等待 [1-9]\d* 秒/).waitFor({ timeout: 4500 });
    if (!await page.getByRole('button', { name: /^取\s*消$/ }).isDisabled()) throw Error('Save can be closed while pending');
    if (await page.evaluate(() => window.validationSaveTimeout) <= 30000) throw Error('Save still uses the global 30-second timeout');
    release(); await page.getByText('保存成功', { exact: true }).waitFor();
    if (writes !== 1) throw Error('Duplicate create requests');
    return { checks: ['Save elapsed seconds advance', 'Cancel is disabled while saving', 'Create request uses a longer timeout', 'Successful save closes the form without a duplicate request'], businessWrites: 0 };
  } finally {
    release(); await page.goto('about:blank'); await page.unrouteAll({ behavior: 'ignoreErrors' });
  }
}
