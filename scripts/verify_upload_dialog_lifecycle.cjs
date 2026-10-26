async(page)=>{
  const checks=[],jobs=new Map(),manifests=[],parts=[];
  const pattern='**/api/image-uploads**';
  let releaseFirst,firstPartSeen,secondStarted,finishFirst=false;
  const gate=new Promise(resolve=>releaseFirst=resolve),seen=new Promise(resolve=>firstPartSeen=resolve),second=new Promise(resolve=>secondStarted=resolve);
  await page.route(pattern,async route=>{
    const request=route.request(),path=new URL(request.url()).pathname,method=request.method();
    if(path.endsWith('/config'))return route.fulfill({json:{chunk_size:256,max_file_size:10*1024**3,max_image_size:10*1024**2,max_expanded_size:20*1024**3,max_entries:10000,retention_days:7}});
    if(path==='/api/image-uploads'&&method==='GET')return route.fulfill({json:[...jobs.values()]});
    if(path==='/api/image-uploads'){
      const data=request.postDataJSON();manifests.push(data);
      const id='11111111-1111-4111-8111-'+String(manifests.length).padStart(12,'0');
      const job={id,filename:data.filename,size:data.size,collection_id:'22222222-2222-4222-8222-222222222222',status:'uploading',uploaded_parts:[],uploaded_bytes:0,cursor:0,error:null,result:{imported:0,duplicates:0,filtered:0,invalid:0,ignored:0,errors:[]}};
      jobs.set(id,job);if(manifests.length===2)secondStarted();
      return route.fulfill({json:job});
    }
    const id=path.split('/')[3],job=jobs.get(id);
    if(!job)throw Error('Unexpected upload route');
    if(path.includes('/parts/')){
      const index=Number(path.split('/').pop());parts.push([job.filename,index]);
      if(parts.length===1){firstPartSeen();await gate;}
      job.uploaded_parts.push(index);job.uploaded_bytes+=request.postDataBuffer().length;
      try{return await route.fulfill({json:{part:index}});}catch{return;}
    }
    if(path.endsWith('/complete')){job.status='pending';return route.fulfill({json:job});}
    job.status=job.filename==='close-first.zip'&&!finishFirst?'processing':'completed';job.cursor=17;
    if(job.status==='completed')job.result.imported=1;
    return route.fulfill({json:job});
  });
  const dialog=()=>page.getByRole('dialog',{name:'上传到图片库',exact:true});
  const open=()=>page.locator('.page-toolbar-actions').getByRole('button',{name:/上传图片/}).click();
  const close=()=>dialog().getByRole('button',{name:'关 闭',exact:true}).click();
  try{
    await page.setViewportSize({width:1440,height:1000});await page.goto('http://localhost:3000/assets');await open();
    await dialog().getByRole('combobox',{name:'上传图片集名称'}).fill('关闭保留测试');
    await dialog().getByRole('spinbutton',{name:'本次上传长边阈值'}).fill('720');
    await dialog().getByLabel('上传图片或压缩包',{exact:true}).setInputFiles([
      {name:'close-first.zip',mimeType:'application/zip',buffer:Buffer.alloc(600,65)},
      {name:'close-second.zip',mimeType:'application/zip',buffer:Buffer.alloc(600,66)}]);
    await close();await dialog().waitFor({state:'hidden'});await open();
    if(manifests.length||await dialog().getByText('等待上传',{exact:true}).count()!==2||await dialog().getByRole('combobox',{name:'上传图片集名称'}).inputValue()!=='关闭保留测试')throw Error('Unstarted selection changed or started on reopen');
    checks.push('unstarted selection survives close/reopen without starting an upload');
    await dialog().getByRole('button',{name:'开始 / 继续上传',exact:true}).click();await seen;
    await close();await dialog().waitFor({state:'hidden'});await open();
    if(!await dialog().getByRole('button',{name:'暂停上传',exact:true}).isVisible())throw Error('Closing the modal aborted the active queue');
    if(await dialog().getByRole('combobox',{name:'上传图片集名称'}).inputValue()!=='关闭保留测试')throw Error('Reopening reset the destination');
    if(await dialog().getByRole('spinbutton',{name:'本次上传长边阈值'}).inputValue()!=='720')throw Error('Reopening reset the resolution policy');
    checks.push('close/reopen during transfer preserves active queue and destination policy');
    releaseFirst();await dialog().getByText('后台入库，已处理 17 项',{exact:true}).waitFor();
    await close();await dialog().waitFor({state:'hidden'});await open();
    await dialog().getByText('后台入库，已处理 17 项',{exact:true}).waitFor();
    checks.push('close/reopen during import preserves actual progress');
    await close();finishFirst=true;await second;
    await open();await dialog().getByText('所选文件已处理完成，可继续添加文件或关闭窗口',{exact:true}).waitFor();
    if(manifests.length!==2||parts.length!==6)throw Error('Hidden queue did not complete both files exactly once');
    checks.push('queue uploads the next file while the modal is hidden');
    await close();await open();
    if(await dialog().getByText(/新增 1，重复 0/).count()!==2)throw Error('Completed rows lost their summaries on reopen');
    await dialog().getByRole('button',{name:'开始 / 继续上传',exact:true}).click();
    if(manifests.length!==2||parts.length!==6)throw Error('Completed files were uploaded again');
    checks.push('completed rows and results survive reopen without duplicate upload');
    await page.screenshot({path:'output/playwright/upload-dialog-lifecycle.png',animations:'disabled'});
    return {checks,manifests:manifests.length,parts:parts.length,scope:'real UI; controlled API gates; all upload writes intercepted'};
  }finally{releaseFirst();await page.goto('about:blank');await page.unroute(pattern);}
}
