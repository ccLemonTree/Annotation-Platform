async page=>{
  const origin='http://192.168.1.90:3000',checks=[];
  const expected=await (async()=>{
    await page.goto('http://localhost:3000/login');
    return page.evaluate(async()=>{
      const bytes=new Uint8Array(8388608).fill(97);
      const digest=await crypto.subtle.digest('SHA-256',bytes);
      return Array.from(new Uint8Array(digest),x=>x.toString(16).padStart(2,'0')).join('');
    });
  })();
  await page.goto(origin+'/assets');
  const hash=await page.evaluate(async()=>{
    const {sha256}=await import('/src/utils/sha256.ts');
    const start=performance.now();
    return {secure:isSecureContext,native:!!crypto.subtle,hash:await sha256(new Uint8Array(8388608).fill(97).buffer),ms:performance.now()-start};
  });
  if(hash.secure||hash.native||hash.hash!==expected)throw Error('LAN fallback did not produce native SHA-256');
  checks.push('Actual LAN HTTP without Web Crypto: 8 MiB SHA-256 equals native result');
  const id='11111111-1111-4111-8111-111111111111',rid='22222222-2222-4222-8222-222222222222';
  let job={id:rid,filename:'filtered.zip',size:3,collection_id:id,min_long_edge:720,status:'completed',uploaded_parts:[0],uploaded_bytes:3,chunk_size:8388608,cursor:94,error:null,result:{imported:0,duplicates:0,filtered:94,invalid:0,ignored:0,errors:[]}};
  const manifests=[],parts=[],filters=[];
  const pattern=/^http:\/\/192\.168\.1\.90:3000\/api\//;
  await page.route(pattern,async route=>{
    const request=route.request(),url=new URL(request.url()),path=url.pathname;
    if(path==='/api/images/collections')return route.fulfill({json:[{id,name:'LAN 上传测试',image_count:0,ingest_config:{min_long_edge:720,classification_enabled:false,scenes:[]}}]});
    if(path==='/api/images'){filters.push(url.search);return route.fulfill({json:{items:[],total:0,page:1,page_size:20}});}
    if(path==='/api/image-uploads/config')return route.fulfill({json:{chunk_size:8388608,max_file_size:10737418240,max_image_size:104857600,max_expanded_size:21474836480,max_entries:10000,retention_days:7}});
    if(path==='/api/image-uploads'&&request.method()==='GET')return route.fulfill({json:[job]});
    if(path==='/api/image-uploads'&&request.method()==='POST'){
      manifests.push(request.postDataJSON());job={...job,filename:'lan.zip',status:'uploading',uploaded_parts:[],uploaded_bytes:0,min_long_edge:0,result:{...job.result,filtered:0}};
      return route.fulfill({json:job});
    }
    if(path.includes('/parts/')){parts.push(request.postDataBuffer().toString());return route.fulfill({json:{part:0}});}
    if(path.endsWith('/complete')){job={...job,status:'completed',uploaded_parts:[0],uploaded_bytes:3,result:{...job.result,imported:1}};return route.fulfill({json:job});}
    if(request.method()!=='GET')throw Error('Unexpected write '+path);
    return route.continue();
  });
  const dialog=page.getByRole('dialog',{name:'上传到图片库',exact:true});
  try{
    await page.reload();await page.locator('.page-toolbar-actions').getByRole('button',{name:/上传图片/}).click();
    await dialog.getByText('无图片入库',{exact:true}).waitFor();
    await dialog.getByText(/尺寸过滤 94 张/).waitFor();
    await dialog.getByText('本次规则：长边 ≥ 720 像素',{exact:true}).waitFor();
    await dialog.getByText('部分压缩包处理完成，但没有图片入库',{exact:true}).waitFor();
    checks.push('History shows zero import, 94 filtered, actual threshold and recovery instructions without file selection');
    await dialog.getByRole('spinbutton',{name:'本次上传长边阈值'}).fill('0');
    await dialog.getByLabel('上传图片或压缩包',{exact:true}).setInputFiles({name:'lan.zip',mimeType:'application/zip',buffer:Buffer.from('abc')});
    await dialog.getByRole('button',{name:'开始 / 继续上传',exact:true}).click();
    await dialog.getByText(/新增 1，重复 0/).waitFor();await dialog.getByText('本次规则：不过滤尺寸',{exact:true}).waitFor();
    if(manifests.length!==1||manifests[0].min_long_edge!==0||manifests[0].chunk_hashes[0]!=='ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad'||parts.join('')!=='abc')throw Error('LAN upload body or checksum incorrect');
    checks.push('LAN selection, SHA-256, chunk upload and zero threshold reach API correctly');
    await dialog.getByRole('button',{name:'清空所选',exact:true}).click();await dialog.getByText(/入库 1 张；已存在 0 张/).waitFor();
    await page.setViewportSize({width:390,height:844});
    if(await dialog.evaluate(el=>el.getBoundingClientRect().right>innerWidth+1))throw Error('Mobile dialog overflow');
    await page.screenshot({path:'output/playwright/lan-upload-mobile.png'});
    const viewed=page.waitForResponse(response=>response.url().includes('collection_id='+id));
    await dialog.getByRole('button',{name:'查看图片集',exact:true}).click();
    await dialog.waitFor({state:'hidden'});await viewed;
    if(!filters.some(query=>query.includes('collection_id='+id)))throw Error('View action did not select destination');
    checks.push('Results survive selection clearing; destination action selects its collection; mobile layout fits');
    return {checks,fallback8MiBMilliseconds:Math.round(hash.ms),scope:'Real LAN browser and hashing; upload writes intercepted'};
  }finally{await page.unroute(pattern);}
}
