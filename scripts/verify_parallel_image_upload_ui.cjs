async page => {
  const origin = process.env.UPLOAD_TEST_ORIGIN || 'http://127.0.0.1:3000';
  const source = await (await page.request.get(origin + '/src/pages/AssetsPage.tsx')).text();
  const react = source.match(/["'](\/node_modules\/\.vite\/deps\/react\.js\?[^"']+)["']/)[1];
  const jobs = new Map();
  const associated = new Set();
  const errors = [];
  let requests = 0, allowImport = false, allowRetry = false, collectionCreated = false, settingsSaved = false;
  const policy = {classification_enabled:true,min_long_edge:720,base_url:'http://fixture/v1',model:'fixture',
    scene_prompt:'识别场景',scenes:['街道'],scene_groups:[{name:'室外',scenes:['街道']}]};
  await page.addInitScript(() => {
    Object.defineProperty(crypto, 'subtle', { value: undefined });
    localStorage.setItem('user', JSON.stringify({role:'admin'}));
  });
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/src/App.tsx*', route => route.fulfill({contentType:'application/javascript',
    body:`import React from '${react}';import Dialog from '/src/components/ImageUploadDialog.tsx';export default function App(){return React.createElement(Dialog,{open:true,initialName:'上传夹具',collections:[],projectId:'fixture',projectName:'批量夹具',taskType:'detection',onImported:()=>{},onClose:()=>{},onViewCollection:()=>{}});}`}));
  await page.route('**/api/**', route => {
    const req = route.request(), path = new URL(req.url()).pathname, method = req.method();
    if (!path.startsWith('/api/')) return route.continue();
    if (path === '/api/image-uploads/config') return route.fulfill({json:{chunk_size:8388608,max_file_size:10*1024**3,max_image_size:100*1024**2,max_expanded_size:20*1024**3,max_entries:200000,retention_days:7}});
    if (path === '/api/images/collections' && method === 'POST') {
      const {name} = req.postDataJSON();
      if (name !== '上传夹具') throw Error('Wrong collection selected from upload');
      collectionCreated = true;
      return route.fulfill({json:{id:'upload-collection',name}});
    }
    if (path === '/api/images/ingest-settings') {
      if (method === 'PUT') {
        const collection = new URL(req.url()).searchParams.get('collection_id'), values = req.postDataJSON();
        if (collection !== 'upload-collection' || values.classification_enabled) throw Error('Wrong rule saved');
        settingsSaved = true;
        return route.fulfill({json:values});
      }
      return route.fulfill({json:policy});
    }
    if (path === '/api/model-services') return route.fulfill({json:[]});
    if (path === '/api/model-services/prompt-defaults') return route.fulfill({json:{scene:policy.scene_prompt}});
    if (path === '/api/images/associate') {
      req.postDataJSON().image_ids.forEach(id => associated.add(id));
      return route.fulfill({json:{added_count:1,conflicts:0}});
    }
    if (path === '/api/image-uploads') {
      if (method === 'GET') return route.fulfill({json:[]});
      requests++;
      const payload = req.postDataJSON(), id = payload.filename;
      if (!/^[a-f0-9]{64}$/.test(payload.chunk_hashes[0])) throw Error('Hash missing on HTTP');
      if (jobs.has(id)) return route.fulfill({json:jobs.get(id)});
      const job = {id, filename:id, size:payload.size, collection_id:'fixture', status:'uploading',
        uploaded_parts:[], uploaded_bytes:0, cursor:0, error:null,
        result:{imported:1,duplicates:0,filtered:0,invalid:0,ignored:0,errors:[]}};
      jobs.set(id, job);
      return route.fulfill({json:job});
    }
    const id = path.split('/')[3], job = jobs.get(id);
    if (path.includes('/parts/')) {
      job.uploaded_parts = [0]; job.uploaded_bytes = job.size;
      return route.fulfill({json:{received:job.size}});
    }
    if (path.endsWith('/complete')) {
      job.status = 'pending';
      return route.fulfill({json:job});
    }
    if (path.endsWith('/images')) return route.fulfill({json:{image_ids:[id],page_size:1000,total:1}});
    if (allowImport) job.status = id === 'fixture-0.jpg' && !allowRetry ? 'failed' : 'completed';
    if (job.status === 'failed') job.error = '夹具入库失败';
    return route.fulfill({json:job});
  });
  await page.goto(origin);
  await page.getByRole('button',{name:'创建图片集并设置',exact:true}).click();
  const settings = page.getByRole('dialog',{name:'图片集入库与场景设置'});
  await settings.getByRole('checkbox',{name:'启用自动场景分类'}).uncheck();
  await settings.getByRole('button',{name:'保存设置',exact:true}).click();
  await page.getByText(/场景分类关闭/).waitFor();
  if (!collectionCreated || !settingsSaved) throw Error('Upload settings did not save for the target collection');
  await page.getByLabel('上传图片或压缩包',{exact:true}).setInputFiles(Array.from({length:18}, (_,i)=>({
    name:`fixture-${i}.jpg`,mimeType:'image/jpeg',buffer:Buffer.from(`fixture-${i}`)})));
  const start = page.getByRole('button',{name:'开始 / 继续上传',exact:true});
  await start.click();
  for (let i=0;i<60 && jobs.size<16;i++) await new Promise(resolve=>setTimeout(resolve,100));
  if (jobs.size !== 16 || associated.size !== 0) throw Error(`Expected exactly 16 in flight before import, got ${jobs.size}`);
  await page.getByText(/正在处理 16/).waitFor({timeout:3000});
  allowImport = true;
  await page.getByText(/1 个失败/).waitFor({timeout:9000});
  await start.waitFor();
  if (jobs.size !== 18 || associated.size !== 17) throw Error('A failed file blocked the other files');
  allowRetry = true;
  await start.click();
  await page.getByText('所选文件已处理完成，可继续添加文件或关闭窗口').waitFor({timeout:9000});
  if (requests !== 19 || associated.size !== 18 || errors.length) throw Error(JSON.stringify({requests,associated:associated.size,errors}));
  return {maxInFlight:16,completed:18,failedFileRetried:true,httpHashFallback:true,settingsSavedFromUpload:true};
}
