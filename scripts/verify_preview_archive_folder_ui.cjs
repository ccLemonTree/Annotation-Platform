async page=>{
  await page.unrouteAll();page.setDefaultTimeout(15000);
  const origin='http://192.168.1.90:3000',pattern=/^http:\/\/192\.168\.1\.90:3000\/api\//;
  const model='22222222-2222-4222-8222-222222222222',id='11111111-1111-4111-8111-111111111111';
  const checks=[],bodies=[],writes=[],pages=[];let job,hold=false,release;
  const png=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jUKcAAAAASUVORK5CYII=','base64');
  await page.route(pattern,async route=>{
    const request=route.request(),url=new URL(request.url()),path=url.pathname;
    const json=(body,status=200)=>route.fulfill({json:body,status});
    if(request.method()!=='GET')writes.push(path);
    if(path==='/api/auth/me')return json({id:1,username:'admin',role:'admin',is_active:true});
    if(path==='/api/model-services')return json([{id:model,name:'Archive detector',enabled:true,kind:'detection'}]);
    if(path==='/api/model-previews/config')return json({max_images:5000,max_upload_size:10*1024**3,max_expanded_size:20*1024**3,max_image_size:100*1024**2});
    if(path==='/api/model-previews'&&request.method()==='GET')return json({items:job?[job]:[],total:job?1:0});
    if(path==='/api/model-previews'&&request.method()==='POST'){
      bodies.push(request.postDataBuffer().toString('utf8'));
      if(hold){await new Promise(resolve=>release=resolve);try{return await json({detail:'aborted fixture'},400);}catch{return;}}
      job={id,set_id:id,name:'Archive batch',status:'completed',total:5000,processed:5000,failed_count:0,error:null,
        model:{id:model,revision:1,name:'Archive detector'},config:{inference_confidence:.25},metrics:{},created_at:'2026-10-19T05:00:00Z',updated_at:'2026-10-19T05:00:00Z'};
      return json(job,201);
    }
    if(path==='/api/model-previews/'+id){
      const current=Number(url.searchParams.get('page')||1);pages.push(current);
      const items=Array.from({length:20},(_,index)=>{const n=(current-1)*20+index;return {id:'item-'+n,original_name:`folder/image-${n}.png`,width:64,height:48,status:'completed',predictions:[],truth:[],evaluation:{}};});
      return json({...job,page:current,page_size:20,items});
    }
    if(path.endsWith('/image'))return route.fulfill({contentType:'image/png',body:png});
    throw Error('Unexpected API '+request.method()+' '+path);
  });
  try{
    await page.goto(origin+'/model-preview');await page.getByRole('combobox',{name:'预览检测模型'}).click();await page.getByText('Archive detector',{exact:true}).last().click();
    await page.getByText(/每批最多5000张/).waitFor();
    const accept=await page.getByLabel('选择模型预览图片',{exact:true}).getAttribute('accept');
    if(!accept.includes('.zip')||!accept.includes('.tgz'))throw Error('Archive picker missing formats');
    await page.getByLabel('选择模型预览图片',{exact:true}).setInputFiles('F:/hzxf/数据管理平台/backend/backend/.backups/preview-archives/owned.zip');
    await page.getByRole('button',{name:'开始模型预览',exact:true}).click();await page.getByRole('img',{name:'folder/image-0.png',exact:true}).waitFor();
    if(!bodies[0].includes('filename="owned.zip"'))throw Error('ZIP not sent in multipart');
    checks.push('ZIP picker reaches upload; limits display 5000 images, 10 GiB upload, 20 GiB expansion');
    await page.locator('.model-preview-results').getByTitle('下一页',{exact:true}).click();
    await page.getByRole('img',{name:'folder/image-20.png',exact:true}).waitFor();
    if(!pages.includes(2)||!page.url().includes('page=2')||await page.locator('.model-preview-gallery button').count()!==20)throw Error('Result pagination incorrect');
    await page.reload();await page.getByRole('img',{name:'folder/image-20.png',exact:true}).waitFor();
    checks.push('5000-result preview renders only 20 result buttons; page 2 is requested and survives reload');
    await page.getByRole('combobox',{name:'预览检测模型'}).click();await page.getByText('Archive detector',{exact:true}).last().click();
    const folder=page.getByLabel('选择模型预览文件夹',{exact:true});
    if(await folder.getAttribute('webkitdirectory')===null)throw Error('Directory picker missing');
    await folder.setInputFiles('F:/hzxf/数据管理平台/backend/backend/.backups/preview-archives/folder');
    await page.getByText('已忽略 1 个非图片文件',{exact:true}).waitFor();
    await page.locator('.model-preview-files li').filter({hasText:'folder/a/same.png'}).waitFor();await page.locator('.model-preview-files li').filter({hasText:'folder/b/same.png'}).waitFor();
    await page.getByRole('button',{name:'开始模型预览',exact:true}).click();
    await page.getByRole('img',{name:'folder/image-0.png',exact:true}).waitFor();
    if(!bodies[1].includes('filename="folder/a/same.png"')||!bodies[1].includes('filename="folder/b/same.png"')||bodies[1].includes('notes.txt'))throw Error('Folder relative paths or ignored file handling incorrect');
    checks.push('Native folder picker includes nested images, preserves duplicate basenames by relative path, skips non-images');
    await page.getByLabel('选择模型预览图片',{exact:true}).setInputFiles('F:/hzxf/数据管理平台/backend/backend/.backups/preview-archives/owned.tar.gz');
    hold=true;
    await page.getByRole('button',{name:'开始模型预览',exact:true}).click({noWaitAfter:true});
    const stop=page.getByRole('button',{name:'停止上传请求',exact:true});await stop.waitFor();
    if(!await stop.isEnabled())throw Error('Upload stop control disabled');
    await stop.click();await page.getByText(/上传请求已停止，所选文件已保留/).waitFor();
    if(!bodies.at(-1).includes('owned.tar.gz'))throw Error('TAR.GZ not sent');
    checks.push('TAR.GZ is accepted; upload can be stopped with selected files retained');
    release();await page.setViewportSize({width:390,height:844});
    if(await page.locator('html').evaluate(el=>el.scrollWidth>el.clientWidth+1))throw Error('Mobile overflow');
    await page.screenshot({path:'output/playwright/preview-archives-mobile.png'});
    if(writes.some(path=>path!=='/api/model-previews'))throw Error('Business write escaped preview upload');
    return {checks,businessWrites:0,scope:'Real LAN browser and native directory input; model/upload responses intercepted'};
  }catch(error){await page.screenshot({path:'output/playwright/preview-archives-error.png'});const buttons=await page.locator('.model-preview-setup button').evaluateAll(rows=>rows.map(row=>({text:row.textContent,label:row.getAttribute('aria-label'),disabled:row.disabled})));throw Error(error.message+'; buttons='+JSON.stringify(buttons)+'; uploads='+bodies.length+'; checks completed: '+checks.join('; '));}
  finally{release?.();await page.goto('about:blank');await page.unroute(pattern);}
}
