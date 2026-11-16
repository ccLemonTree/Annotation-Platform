/* All API traffic is mocked. Run only against a dedicated local Vite instance. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

async function verify(page, origin = 'http://127.0.0.1:3108') {
  assert.ok(['127.0.0.1', 'localhost'].includes(new URL(origin).hostname));
  const checks = [], requests = [], unexpected = [], errors = [];
  const out = path.resolve(__dirname, '../output/playwright');
  fs.mkdirSync(out, { recursive: true });
  let role = 'admin', failUpload = false, failDownload = false, failList = false, failEdit = false, failConfig = false, failVersions = false, stage = 'initial';
  const configText='name: "fire"\nplatform: "tensorrt_plan"\n# <script>window.untrustedConfig=true</script>\n';
  const groupName=name=>name.replace(/ · (TENSORRT|ONNX|PT)$/i,'').trimEnd();
  const runId = '10000000-0000-4000-8000-000000000001';
  const source = { id: runId, name: '完成训练夹具', kind: 'yolo', created_at: '2026-10-28T01:00:00Z' };
  const artifact = { path: 'export/model.onnx', bytes: 1024, format: 'onnx' };
  const makeModel = (id, format = 'pt', extra = {}) => ({ id, name: `模型夹具${id}`, version: 'v1', format,
    description: '', gpu_model: '', batch_profile:'', entry_file: format === 'tensorrt' ? 'model.engine' : `model.${format}`, file_size: 1024,
    files: [{ path: `model.${format}`, bytes: 1024, sha256: 'a'.repeat(64) }], status: 'ready',
    created_at: '2026-10-28T01:00:00Z', updated_at: '2026-10-28T01:00:00Z', ...extra });
  let models = [makeModel('pt'), makeModel('onnx', 'onnx', { files: [
    { path: 'model.onnx', bytes: 1024, sha256: 'a'.repeat(64) }, { path: 'weights.data', bytes: 2048, sha256: 'b'.repeat(64) }], file_size: 3072 }),
    makeModel('t4', 'tensorrt', { name: '同版本引擎', gpu_model: 'NVIDIA T4', tensorrt_version: '10.8.0',files:[{path:'model.engine',bytes:1024,sha256:'a'.repeat(64)},{path:'config.pbtxt',bytes:configText.length,sha256:'b'.repeat(64)}] }),
    makeModel('a100', 'tensorrt', { name: '同版本引擎', gpu_model: 'NVIDIA A100' }),
    makeModel('failed', 'pt', { status: 'failed', run_id: runId, error: '夹具复制失败' }),
    makeModel('interrupted', 'pt', { status: 'interrupted', run_id: runId, error: '进程中断' }),
    makeModel('importing', 'pt', { status: 'importing', run_id: runId }),
    ...Array.from({ length: 16 }, (_, i) => makeModel(`extra-${i}`)),
    ...['pt','onnx','tensorrt'].map((format,i)=>makeModel('fire-'+format,format,{name:'detr1023烟火训练'+(i?' · '+format.toUpperCase():''),gpu_model:i===2?'Tesla T4':'',version:'v1023'}))];
  models.find(model=>model.id==='fire-tensorrt').batch_profile='fixed:1';
  models.push(makeModel('fire-b8','tensorrt',{name:'detr1023烟火训练 · TENSORRT',version:'v1023',gpu_model:'Tesla T4',batch_profile:'fixed:8'}));
  models=[...models.filter(model=>model.id.startsWith('fire-')),...models.filter(model=>!model.id.startsWith('fire-'))];
  const handler = async route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname, method = req.method();
    if (p.startsWith('/api/')) {
      requests.push({ method, path: p, query: Object.fromEntries(url.searchParams), authorization: req.headers().authorization,
        body: req.postData() || '' });
      const json = (data, status = 200) => route.fulfill({ status, json: data });
      if (p === '/api/auth/me') return json({ id: 1, tenant_id: 1, username: '仓库隔离夹具', role });
      if ((p === '/api/model-repository' || p === '/api/model-repository/groups') && method === 'GET') {
        if (failList) return json({ detail: '夹具列表暂不可用' }, 503);
        if (p === '/api/model-repository' && failVersions) return json({detail:'夹具版本暂不可用'},503);
        const q = url.searchParams.get('q') || '', format = url.searchParams.get('format'), gpu = url.searchParams.get('gpu_model');
        const group=url.searchParams.get('group_name');
        const filtered = models.filter(m => (!q || m.name.includes(q)) && (!format || m.format === format) && (!gpu || m.gpu_model === gpu) && (group===null || groupName(m.name)===group));
        const current = Number(url.searchParams.get('page') || 1);
        if(p.endsWith('/groups')) {
          const groups=[...new Set(filtered.map(m=>groupName(m.name)))].map(name=>{
            const variants=filtered.filter(m=>groupName(m.name)===name);
            return {name,version_count:variants.length,formats:[...new Set(variants.map(m=>m.format))],gpu_models:[...new Set(variants.map(m=>m.gpu_model).filter(Boolean))],latest_at:variants[0].created_at,importing_count:variants.filter(m=>m.status==='importing').length};
          });
          return json({items:groups.slice((current-1)*20,current*20),total:groups.length,version_total:filtered.length,gpu_models:['NVIDIA T4','NVIDIA A100','Tesla T4']});
        }
        return json({ items: filtered.slice((current - 1) * 20, current * 20), total: filtered.length, page: current, page_size: 20, gpu_models: ['NVIDIA T4', 'NVIDIA A100'] });
      }
      if (p === '/api/model-repository/sources') {
        const rows = Array.from({ length: 21 }, (_, i) => ({ ...source, id: i ? `source-${i}` : runId, name: i ? `完成任务${i}` : source.name }));
        return json({ items: rows.slice((Number(url.searchParams.get('page')) - 1) * 20, Number(url.searchParams.get('page')) * 20), total: 21 });
      }
      if (/\/sources\/[^/]+\/artifacts$/.test(p)) return json({ items: [artifact, { path: 'best.pt', bytes: 1024, format: 'pt' }], truncated: true });
      if (p === `/api/training/runs/${runId}`) return json({ ...source, status: 'completed' });
      if (p === '/api/model-repository/upload' && method === 'POST') {
        if (failUpload) return json({ detail: '夹具版本已存在' }, 409);
        return json(makeModel('uploaded'), 201);
      }
      if (p === '/api/model-repository/from-training' && method === 'POST') {
        const body = req.postDataJSON(), model = makeModel('import-created', body.format, { ...body, status: 'importing', entry_file: body.artifact_path });
        models = [model, ...models]; return json(model, 201);
      }
      if (p.startsWith('/api/model-repository/') && method === 'PATCH') {
        if (failEdit) return json({ detail: '同一模型版本已存在' }, 409);
        const model = models.find(m => m.id === p.split('/').at(-1));
        Object.assign(model, req.postDataJSON()); return json(model);
      }
      if (p.endsWith('/retry') && method === 'POST') {
        const id = p.split('/').at(-2), model = models.find(m => m.id === id);
        model.status = 'importing'; model.error = ''; return json(model, 202);
      }
      if (p.endsWith('/download') && method === 'GET') {
        if (failDownload) return json({ detail: '夹具文件校验失败' }, 409);
        return route.fulfill({ contentType: p.includes('/onnx/') ? 'application/zip' : 'application/octet-stream', body: Buffer.from('isolated model fixture') });
      }
      if(p.endsWith('/config') && method==='GET')return failConfig?json({detail:'夹具配置校验失败'},409):json({content:configText,sha256:'b'.repeat(64)});
      if (p === '/api/projects') return json({ items: [], total: 0 });
      unexpected.push(`${method} ${p}`); return json({ detail: 'Unexpected API fixture' }, 500);
    }
    if (url.origin !== origin) { unexpected.push(req.url()); return route.abort(); }
    return route.continue();
  };
  await page.context().route('**/*', handler);
  await page.context().grantPermissions(['clipboard-read', 'clipboard-write'], { origin });
  await page.addInitScript(() => {
    if (!['http:', 'https:'].includes(location.protocol)) return;
    localStorage.setItem('token', 'fixture-only');
    if (!localStorage.getItem('user')) localStorage.setItem('user', JSON.stringify({ id: 1, tenant_id: 1, username: '仓库隔离夹具', role: 'admin' }));
  });
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => { if (message.text().includes('Instance created by `useForm`')) errors.push(message.text()); });
  page.setDefaultTimeout(12000);
  const row = id => page.locator(`.repository-versions tr[data-row-key="${id}"]`);
  const expand=async name=>{
    if(await page.getByRole('button',{name:'收起模型 '+name,exact:true}).count())return;
    const toggle=page.getByRole('button',{name:'展开模型 '+name,exact:true});await toggle.waitFor();await toggle.click();
  };
  const choose = async (scope, label, text) => {
    const input = scope.getByRole('combobox', { name: new RegExp(label + '$') });
    await input.scrollIntoViewIfNeeded();
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    await input.click();
    const dropdown = page.locator('.ant-select-dropdown:visible');
    await dropdown.locator('.ant-select-item-option-content').getByText(text, { exact: true }).waitFor();
    const options = await dropdown.locator('.ant-select-item-option-content').allTextContents();
    const index = options.findIndex(value => value.trim() === text);
    assert.ok(index >= 0, `Missing option ${text}: ${JSON.stringify(options)}`);
    await input.press('Home');
    for (let i = 0; i < index; i++) await input.press('ArrowDown');
    await input.press('Enter'); await dropdown.waitFor({ state: 'hidden' });
  };
  const upload = async (format, files, name) => {
    await page.getByRole('button', { name: /上传模型$/ }).click();
    const modal = page.getByRole('dialog', { name: '上传模型到仓库', exact: true });
    await modal.getByRole('textbox', { name: /模型名称$/ }).fill(name);
    await modal.getByRole('textbox', { name: /版本号$/ }).fill('v1');
    if (format !== 'PT') await choose(modal, '模型格式', format);
    await modal.locator('input[type=file]').first().setInputFiles(files.map(name => ({ name, mimeType: 'application/octet-stream', buffer: Buffer.from('fixture bytes') })));
    await choose(modal, '主模型文件', files[0]); return modal;
  };
  try {
    await page.emulateMedia({reducedMotion:'reduce'});await page.setViewportSize({width:1440,height:1000});
    await page.goto(`${origin}/model-repository`);
    await page.getByText('共 23 组模型',{exact:true}).waitFor();
    assert.equal(await row('fire-pt').count(),0);
    await expand('detr1023烟火训练');
    for(const id of ['fire-pt','fire-onnx','fire-tensorrt','fire-b8'])await row(id).waitFor();
    await row('fire-tensorrt').getByText('固定 batch 1',{exact:true}).waitFor();await row('fire-b8').getByText('固定 batch 8',{exact:true}).waitFor();
    assert.equal(requests.filter(r=>r.path==='/api/model-repository').at(-1).query.group_name,'detr1023烟火训练');
    await page.screenshot({path:path.join(out,'model-repository-grouped-desktop.png')});
    checks.push('PT/ONNX/TRT suffix variants form one collapsible group; versions fetched lazily by exact group');
    stage='config preview';await expand('同版本引擎');await row('t4').waitFor();
    await row('a100').getByText('未附带 config.pbtxt',{exact:true}).waitFor();
    await row('t4').getByRole('button',{name:'查看 config.pbtxt',exact:true}).click();
    let modal=page.getByRole('dialog',{name:'config.pbtxt',exact:true});
    assert.equal(await modal.locator('pre').innerText(),configText);
    assert.equal(await page.evaluate(()=>window.untrustedConfig),undefined);
    await page.screenshot({path:path.join(out,'model-repository-config-preview.png')});
    await modal.getByRole('button',{name:'关闭配置',exact:true}).click();
    failConfig=true;await row('t4').getByRole('button',{name:'查看 config.pbtxt',exact:true}).click();
    await modal.getByText('夹具配置校验失败',{exact:true}).waitFor();await modal.getByRole('button',{name:'关闭配置',exact:true}).click();failConfig=false;
    checks.push('config preview is escaped text, missing config is explicit, verification failure visible');
    stage='pagination filters';await page.goto(`${origin}/model-repository`);await page.getByTitle('2',{exact:true}).click();
    await page.getByRole('button',{name:'展开模型 模型夹具extra-15',exact:true}).waitFor();
    assert.equal(requests.filter(r=>r.path.endsWith('/groups')).at(-1).query.page,'2');
    await choose(page,'筛选模型格式','TensorRT');await page.getByText('共 2 组模型',{exact:true}).waitFor();
    await choose(page,'筛选GPU型号','NVIDIA T4');await page.getByText('共 1 组模型',{exact:true}).waitFor();
    await page.getByLabel('搜索模型名称',{exact:true}).fill('不存在');await page.getByLabel('搜索模型名称',{exact:true}).press('Enter');
    await page.getByText('没有符合条件的模型',{exact:true}).waitFor();
    checks.push('group pagination, format/GPU/name filters and empty result');
    stage='metadata edit';await page.goto(`${origin}/model-repository`);await expand('模型夹具pt');await row('pt').waitFor();
    await row('pt').getByRole('button',{name:'编辑',exact:true}).click();
    let editor=page.getByRole('dialog',{name:'版本配置',exact:true});
    assert.equal(await editor.getByRole('textbox',{name:/模型名称$/}).inputValue(),'模型夹具pt');
    await editor.getByRole('textbox',{name:/版本号$/}).fill('v2');await editor.getByRole('textbox',{name:/备注$/}).fill('园区烟火检测\n已验收');
    failEdit=true;await editor.getByRole('button',{name:'保存修改',exact:true}).click();await editor.getByText('同一模型版本已存在',{exact:true}).waitFor();
    assert.equal(await editor.getByRole('textbox',{name:/备注$/}).inputValue(),'园区烟火检测\n已验收');
    failEdit=false;await editor.getByRole('button',{name:'保存修改',exact:true}).click();await editor.waitFor({state:'hidden'});
    await row('pt').getByText('v2',{exact:true}).waitFor();
    assert.deepEqual(Object.keys(JSON.parse(requests.filter(r=>r.method==='PATCH').at(-1).body)).sort(),['description','name','version']);
    await row('pt').getByRole('button',{name:'编辑',exact:true}).click();editor=page.getByRole('dialog',{name:'版本配置',exact:true});
    await editor.getByRole('textbox',{name:/备注$/}).fill('未保存');await editor.getByRole('button',{name:'取消',exact:true}).click();
    assert.equal(models.find(m=>m.id==='pt').description,'园区烟火检测\n已验收');
    checks.push('edit prefills, conflict preserves input, save refreshes expanded versions, cancel preserves metadata');
    stage='download and files';await expand('模型夹具onnx');await row('onnx').waitFor();
    const downloaded=page.waitForEvent('download');await row('onnx').getByRole('button',{name:'下载 ZIP',exact:true}).click();
    assert.equal((await downloaded).suggestedFilename(),'模型夹具onnx-v1.zip');
    await row('pt').getByRole('button',{name:'更多操作',exact:true}).click();await page.getByRole('menuitem',{name:'复制下载接口',exact:true}).click();
    assert.equal(await page.evaluate(()=>navigator.clipboard.readText()),`${origin}/api/model-repository/pt/download`);
    failDownload=true;await row('pt').getByRole('button',{name:'下载',exact:true}).click();await page.getByText('夹具文件校验失败',{exact:true}).first().waitFor();failDownload=false;
    await row('onnx').getByRole('button',{name:'文件与 SHA256',exact:true}).click();
    await page.getByRole('dialog').getByText('weights.data',{exact:true}).waitFor();await page.getByRole('dialog').getByRole('button',{name:'关闭',exact:true}).last().click();
    checks.push('ZIP/file details and Bearer download retained; copied URL excludes credentials');
    stage='uploads';modal=await upload('PT',['best.pth'],'PT上传夹具');
    await modal.getByRole('button',{name:/上传并保存$/}).click();await modal.waitFor({state:'hidden'});
    modal=await upload('ONNX',['model.onnx','weights.data'],'ONNX上传夹具');failUpload=true;
    await modal.getByRole('button',{name:/上传并保存$/}).click();await modal.getByText('夹具版本已存在',{exact:true}).waitFor();
    assert.equal(await modal.getByRole('textbox',{name:/模型名称$/}).inputValue(),'ONNX上传夹具');failUpload=false;
    await modal.getByRole('button',{name:/上传并保存$/}).click();await modal.waitFor({state:'hidden'});
    modal=await upload('TensorRT',['model.engine','config.pbtxt'],'TensorRT配套配置夹具');
    await modal.getByText(/可同时选择根目录的 config.pbtxt/).waitFor();
    await modal.getByRole('button',{name:/上传并保存$/}).click();await modal.getByText('请输入构建此模型的 GPU 型号',{exact:true}).waitFor();
    await modal.getByRole('textbox',{name:/GPU 型号$/}).fill('Tesla T4');
    await choose(modal,'Batch 类型','固定 batch');await modal.getByRole('spinbutton',{name:/固定 batch$/}).fill('8');
    await modal.getByRole('button',{name:/上传并保存$/}).click();await modal.waitFor({state:'hidden'});
    const uploaded=requests.filter(r=>r.path.endsWith('/upload')).at(-1).body;
    assert.match(uploaded,/filename="config.pbtxt"/);assert.match(uploaded,/filename="model.engine"/);
    assert.match(uploaded,/name="batch_profile"\r\n\r\nfixed:8/);
    modal=await upload('TensorRT',['dynamic.engine'],'动态batch夹具');await modal.getByRole('textbox',{name:/GPU 型号$/}).fill('Tesla T4');
    await choose(modal,'Batch 类型','动态 batch');
    await modal.getByRole('spinbutton',{name:/最小 batch$/}).fill('1');await modal.getByRole('spinbutton',{name:/最佳 batch$/}).fill('4');await modal.getByRole('spinbutton',{name:/最大 batch$/}).fill('8');
    await modal.getByRole('button',{name:/上传并保存$/}).click();await modal.waitFor({state:'hidden'});
    assert.match(requests.filter(r=>r.path.endsWith('/upload')).at(-1).body,/name="batch_profile"\r\n\r\ndynamic:1:4:8/);
    checks.push('same-version/GPU fixed batch variants display separately; fixed and dynamic upload metadata encoded');
    modal=await upload('TensorRT',['model.engine'],'过大配置夹具');
    await modal.getByRole('textbox',{name:/GPU 型号$/}).fill('Tesla T4');
    await modal.locator('input[type=file]').first().setInputFiles([{name:'config.pbtxt',mimeType:'text/plain',buffer:Buffer.alloc(1024**2+1,120)}]);
    const uploadCount=requests.filter(r=>r.path.endsWith('/upload')).length;
    await modal.getByRole('button',{name:/上传并保存$/}).click();await modal.getByText('config.pbtxt不能超过1 MiB',{exact:true}).waitFor();
    assert.equal(requests.filter(r=>r.path.endsWith('/upload')).length,uploadCount);await modal.getByRole('button',{name:/^取\s*消$/}).click();
    checks.push('PT/ONNX uploads and errors retained; TensorRT uploads engine+config together and requires GPU');
    stage='training import';await page.getByRole('button',{name:'从训练产物入库',exact:true}).click();
    modal=page.getByRole('dialog',{name:'从已完成训练产物入库',exact:true});
    await modal.getByRole('button',{name:'加载更多已完成任务',exact:true}).click();await modal.getByRole('button',{name:'加载更多已完成任务',exact:true}).waitFor({state:'hidden'});
    await choose(modal,'已完成任务','完成训练夹具 · yolo');await choose(modal,'模型产物','export/model.onnx · 1.00 KiB');
    await modal.getByRole('textbox',{name:/版本号$/}).fill('v2');await modal.getByRole('button',{name:'开始入库',exact:true}).click();await modal.waitFor({state:'hidden'});
    await expand('模型夹具failed');await row('failed').getByRole('button',{name:'重试入库',exact:true}).click();
    assert.ok(requests.some(r=>r.path.endsWith('/failed/retry')));
    await page.goto(`${origin}/model-repository?run_id=${runId}`);modal=page.getByRole('dialog',{name:'从已完成训练产物入库',exact:true});await modal.waitFor();
    assert.equal(await modal.getByRole('textbox',{name:/模型名称$/}).inputValue(),source.name);await modal.getByRole('button',{name:/^取\s*消$/}).click();
    checks.push('training source pagination, import/retry and deep link preserved');
    stage='retry errors';failList=true;await page.getByRole('button',{name:/刷\s*新$/}).click();await page.getByText('夹具列表暂不可用',{exact:true}).first().waitFor();
    failList=false;await page.getByRole('button',{name:/刷\s*新$/}).click();await page.getByRole('button',{name:'展开模型 模型夹具pt',exact:true}).waitFor();
    failVersions=true;await expand('模型夹具pt');await page.getByText('夹具版本暂不可用',{exact:true}).first().waitFor();
    failVersions=false;await page.getByRole('button',{name:'重试版本',exact:true}).click();await row('pt').waitFor();
    checks.push('group and expanded-version API failures can retry independently');
    stage='mobile';await page.setViewportSize({width:390,height:844});await page.waitForFunction(()=>document.documentElement.scrollWidth<=innerWidth);
    await page.screenshot({path:path.join(out,'model-repository-grouped-mobile.png')});
    await row('pt').getByRole('button',{name:'编辑',exact:true}).click();await page.getByRole('dialog',{name:'版本配置',exact:true}).waitFor();
    await page.waitForFunction(()=>document.documentElement.scrollWidth<=innerWidth);await page.screenshot({path:path.join(out,'model-repository-edit-mobile.png')});
    await page.getByRole('dialog',{name:'版本配置',exact:true}).getByRole('button',{name:'取消',exact:true}).click();
    role='annotator';await page.evaluate(()=>localStorage.setItem('user',JSON.stringify({id:1,tenant_id:1,role:'annotator'})));
    await page.goto(`${origin}/model-repository`);await expand('模型夹具pt');await row('pt').waitFor();
    assert.equal(await page.getByRole('button',{name:/上传模型$/}).count(),0);assert.equal(await page.getByRole('button',{name:'编辑',exact:true}).count(),0);
    assert.ok(await row('pt').getByRole('button',{name:'下载',exact:true}).isEnabled());
    checks.push('390px grouped table/edit drawer fit viewport; reader retains download without mutation controls');
    assert.deepEqual(unexpected,[]);assert.deepEqual(errors,[]);
    return {checks,requests:requests.length,mockedWrites:requests.filter(r=>['POST','PATCH'].includes(r.method)).length,unexpected,runtimeErrors:errors,
      scope:'All API responses mocked; dedicated local Vite; no business database or real SSH/GPU access.'};
  } catch (error) {
    fs.writeFileSync(path.join(out, 'model-repository-failure-aria.txt'), await page.locator('body').ariaSnapshot());
    await page.screenshot({ path: path.join(out, 'model-repository-failure.png'), fullPage: true });
    throw Error(`${stage}: ${error.message}; unexpected=${JSON.stringify(unexpected)}; runtime=${JSON.stringify(errors)}`);
  } finally { await page.goto('about:blank'); await page.context().unroute('**/*', handler); }
}
module.exports = verify;
if (require.main === module) {
  const pw = require(path.join(process.env.USERPROFILE, '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));
  (async () => {
    const browser = await pw.chromium.launch({ channel: 'msedge', headless: true });
    try {
      const context = await browser.newContext({ serviceWorkers: 'block', acceptDownloads: true, timezoneId: 'Asia/Shanghai' });
      const result = await verify(await context.newPage(), process.argv[2]);
      fs.writeFileSync(path.resolve(__dirname, '../output/playwright/model-repository-verification.json'), JSON.stringify(result, null, 2));
      console.log(JSON.stringify(result, null, 2));
    } finally { await browser.close(); }
  })().catch(error => { console.error(error); process.exitCode = 1; });
}
