async(page)=>{
  const checks=[],parts=[],manifests=[],errors=[];
  const routePattern='**/api/image-uploads**';
  let failures=3,pauseNext=false,job=null;
  const config={chunk_size:256,max_file_size:10*1024**3,max_image_size:10*1024**2,max_expanded_size:20*1024**3,max_entries:10000,retention_days:7};
  await page.route(routePattern,async route=>{
    const request=route.request(),path=new URL(request.url()).pathname,method=request.method();
    if(path.endsWith('/config'))return route.fulfill({json:config});
    if(path==='/api/image-uploads'&&method==='GET')return route.fulfill({json:job?[job]:[]});
    if(path==='/api/image-uploads'&&method==='POST'){
      const data=request.postDataJSON();manifests.push(data);
      if(!job)job={id:'11111111-1111-4111-8111-111111111111',filename:data.filename,size:data.size,collection_id:'22222222-2222-4222-8222-222222222222',status:'uploading',uploaded_parts:[],uploaded_bytes:0,chunk_size:256,cursor:0,error:null,result:{imported:0,duplicates:0,filtered:0,invalid:0,ignored:0,errors:[]}};
      return route.fulfill({json:job});
    }
    if(path.includes('/parts/')){
      const index=Number(path.split('/').pop());parts.push(index);
      if(!request.headers().authorization?.startsWith('Bearer '))throw Error('Chunk missing authorization');
      if(!request.headers()['content-type'].startsWith('application/octet-stream'))throw Error('Wrong binary content type');
      const bytes=request.postDataBuffer();
      if(bytes.length!==Math.min(256,job.size-index*256))throw Error('Wrong chunk size');
      if(failures&&index===1){failures--;return route.fulfill({status:503,json:{detail:'模拟网络中断'}});}
      if(pauseNext){pauseNext=false;await new Promise(resolve=>setTimeout(resolve,1500));}
      if(!job.uploaded_parts.includes(index)){job.uploaded_parts.push(index);job.uploaded_bytes+=bytes.length;}
      try{return await route.fulfill({json:{part:index,received:bytes.length}});}catch{return;}
    }
    if(path.endsWith('/complete')){job.status='pending';return route.fulfill({json:job});}
    if(method==='DELETE'){job=null;return route.fulfill({status:204});}
    job.status='completed';job.cursor=2;job.result.imported=1;job.result.duplicates=1;
    return route.fulfill({json:job});
  });
  const recordError=error=>errors.push(error.message);page.on('pageerror',recordError);
  const dialog=()=>page.getByRole('dialog',{name:'上传到图片库',exact:true});
  const start=()=>dialog().getByRole('button',{name:'开始 / 继续上传',exact:true});
  const choose=async()=>{
    await page.locator('.page-toolbar-actions').getByRole('button',{name:/上传图片/}).click();
    await dialog().getByRole('combobox',{name:'上传图片集名称'}).fill('续传UI测试');
    await dialog().getByRole('spinbutton',{name:'本次上传长边阈值'}).fill('720');
    await dialog().getByLabel('上传图片或压缩包',{exact:true}).setInputFiles({name:'fixture.zip',mimeType:'application/zip',buffer:Buffer.alloc(600,65)});
  };
  try{
    await page.setViewportSize({width:1440,height:1000});await page.goto('http://localhost:3000/assets');await choose();
    if(await dialog().getByLabel('上传图片文件夹',{exact:true}).getAttribute('webkitdirectory')===null)throw Error('Folder picker missing');
    checks.push('folder picker and ZIP/TAR selection available');
    await start().click();await dialog().getByText(/已重试.*继续上传/).waitFor();
    if(parts.join(',')!=='0,1,1,1')throw Error('Interruption fixture failed');
    checks.push('interrupted transfer retains uploaded first part');
    await page.reload();await choose();await start().click();await dialog().getByText(/新增 1，重复 1/).waitFor();
    if(parts.join(',')!=='0,1,1,1,1,2')throw Error('Resume retransmitted a received part');
    if(manifests.length!==2||JSON.stringify(manifests[0])!==JSON.stringify(manifests[1])||manifests[0].chunk_hashes.length!==3||manifests[0].chunk_hashes.some(hash=>!/^[a-f0-9]{64}$/.test(hash)))throw Error('Manifest not stable across refresh');
    if(manifests[1].min_long_edge!==720||manifests[1].collection_name!=='续传UI测试')throw Error('Target policy missing');
    checks.push('browser refresh and file reselection upload only missing chunks with stable SHA256 manifest');
    checks.push('collection and inclusive 720 policy reach initialization');
    await dialog().getByRole('button',{name:'关 闭',exact:true}).click();
    await page.locator('.page-toolbar-actions').getByRole('button',{name:/上传图片/}).click();
    await start().click();
    await dialog().getByText(/新增 1，重复 1/).waitFor();
    if(manifests.length!==2)throw Error('Reopening submitted a completed file again');
    checks.push('reopening retains completed progress and destination');
    await dialog().getByRole('combobox',{name:'上传图片集名称'}).fill('另一个目标图片集');
    await dialog().getByText('等待上传',{exact:true}).waitFor();
    await start().click();await dialog().getByText(/新增 1，重复 1/).waitFor();
    if(manifests.length!==3||manifests[2].collection_name!=='另一个目标图片集')throw Error('Changing destination did not revalidate the file');
    checks.push('explicit destination changes reset progress and initialize the new target');
    await dialog().getByRole('spinbutton',{name:'本次上传长边阈值'}).fill('0');
    await dialog().getByText('等待上传',{exact:true}).waitFor();
    await start().click();await dialog().getByText(/新增 1，重复 1/).waitFor();
    if(manifests.length!==4||manifests[3].min_long_edge!==0)throw Error('Changing threshold did not revalidate the file');
    checks.push('explicit resolution changes reset progress and preserve zero override');
    await dialog().getByRole('button',{name:'清除记录',exact:true}).click();
    job=null;pauseNext=true;
    await dialog().getByRole('button',{name:'清空所选',exact:true}).click();
    await dialog().getByLabel('上传图片或压缩包',{exact:true}).setInputFiles({name:'fixture.zip',mimeType:'application/zip',buffer:Buffer.alloc(600,65)});
    await start().click();await dialog().getByRole('button',{name:'暂停上传',exact:true}).click();
    await dialog().getByText(/已暂停传输/).waitFor();
    checks.push('pause stops browser work and keeps resumable receipt');
    await page.setViewportSize({width:390,height:844});
    if(await page.locator('html').evaluate(el=>el.scrollWidth>el.clientWidth+1))throw Error('Page overflows narrow viewport');
    if(await dialog().evaluate(el=>el.getBoundingClientRect().right>innerWidth+1))throw Error('Dialog overflows narrow viewport');
    if(await start().evaluate(el=>el.getBoundingClientRect().bottom>innerHeight))throw Error('Upload actions fall below viewport');
    checks.push('upload actions remain visible while dialog contents scroll');
    await page.screenshot({path:'output/playwright/resumable-upload-mobile.png'});
    checks.push('upload dialog fits narrow viewport');
    await page.setViewportSize({width:1440,height:1000});await page.screenshot({path:'output/playwright/resumable-upload-desktop.png'});
    if(errors.length)throw Error('Browser errors: '+errors.join('; '));
    checks.push('no uncaught browser errors');
    return {checks,scope:'actual UI and Web Crypto; all upload writes intercepted; no real business data created',parts};
  }finally{page.off('pageerror',recordError);await page.unroute(routePattern);}
}
