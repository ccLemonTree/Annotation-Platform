async page=>{
  await page.unrouteAll();page.setDefaultTimeout(8000);
  const origin='http://127.0.0.1:3000',checks=[],writes=[],errors=[];
  const recordError=error=>errors.push(error.message);page.on('pageerror',recordError);
  let mode='transient',job=null,parts=[],completions=0;
  const config={chunk_size:256,max_file_size:10737418240,max_image_size:104857600,max_expanded_size:21474836480,max_entries:10000,retention_days:7};
  await page.addInitScript(()=>{if(location.origin==='http://127.0.0.1:3000'){localStorage.setItem('token','isolated-network-fixture');localStorage.setItem('user',JSON.stringify({role:'admin'}));}});
  await page.route('**/src/App.tsx*',async route=>{
    const source=await (await page.request.get(origin+'/src/components/ImageUploadDialog.tsx')).text();
    const react=source.match(/["'](\/node_modules\/\.vite\/deps\/react\.js\?[^"']+)["']/)[1];
    await route.fulfill({contentType:'application/javascript',body:`import React from '${react}';import Provider from '/src/ui/PlatformProvider.tsx';import Upload from '/src/components/ImageUploadDialog.tsx';export default function App(){return React.createElement(Provider,null,React.createElement(Upload,{open:true,initialName:'网络重试测试',collections:[],onClose(){},onImported(){},onViewCollection(){}}));}`});
  });
  await page.route('**/api/**',async route=>{
    const request=route.request(),path=new URL(request.url()).pathname,method=request.method();
    if(!path.startsWith('/api/'))return route.continue();
    if(method!=='GET')writes.push({path,method});
    if(path==='/api/images/ingest-settings')return route.fulfill({json:{min_long_edge:0,classification_enabled:false}});
    if(path==='/api/image-uploads/config')return route.fulfill({json:config});
    if(path==='/api/image-uploads'&&method==='GET')return route.fulfill({json:job?[job]:[]});
    if(path==='/api/image-uploads'&&method==='POST'){
      const input=request.postDataJSON();
      if(!job)job={...input,id:'11111111-1111-4111-8111-111111111111',collection_id:'22222222-2222-4222-8222-222222222222',min_long_edge:0,status:'uploading',chunk_size:256,uploaded_parts:[],uploaded_bytes:0,cursor:0,error:null,result:{imported:0,duplicates:0,filtered:0,invalid:0,ignored:0,errors:[]}};
      return route.fulfill({json:job});
    }
    if(path.includes('/parts/')){
      const index=Number(path.split('/').pop());parts.push(index);
      const count=parts.filter(value=>value===index).length;
      if(index===1&&mode==='invalid')return route.fulfill({status:422,json:{detail:'分片校验不匹配'}});
      if(index===1&&mode==='unavailable'&&count===1)return route.fulfill({status:503,json:{detail:'临时不可用'}});
      const fail=index===1&&(mode==='persistent'||mode==='pause'||(mode==='transient'||mode==='lost-response')&&count===1);
      if(!fail||mode==='lost-response'){
        if(!job.uploaded_parts.includes(index)){job.uploaded_parts.push(index);job.uploaded_bytes+=request.postDataBuffer().length;}
      }
      if(fail)return route.abort('connectionreset');
      return route.fulfill({json:{part:index}});
    }
    if(path.endsWith('/complete')){completions++;job.status='completed';job.result.imported=1;return route.fulfill({json:job});}
    throw Error('Unexpected fixture API '+method+' '+path);
  });
  const dialog=()=>page.getByRole('dialog',{name:'上传到图片库',exact:true});
  const start=()=>dialog().getByRole('button',{name:'开始 / 继续上传',exact:true});
  const choose=async value=>{
    mode=value;job=null;parts=[];completions=0;
    await page.goto(origin+'/assets');
    await dialog().getByLabel('上传图片或压缩包',{exact:true}).setInputFiles({name:'network-fixture.zip',mimeType:'application/zip',buffer:Buffer.alloc(600,65)});
    await start().click();
  };
  let stage='transient connection reset';
  try{
    await choose('transient');
    await dialog().getByText(/连接中断.*重试/).waitFor();
    await dialog().getByText(/新增 1，重复 0/).waitFor();
    if(parts.join(',')!=='0,1,1,2'||completions!==1)throw Error('Failed chunk was not retried independently');
    if(await page.locator('.ant-message-error').count())throw Error('Recovered failure displayed a global error toast');
    checks.push('connection reset retries only the failed part and completes without an error toast');

    stage='persistent failure and resume';await choose('persistent');
    await dialog().getByText(/已重试.*继续上传/).waitFor();
    if(parts.join(',')!=='0,1,1,1'||completions)throw Error('Retry limit did not preserve uploaded parts');
    mode='normal';await start().click();await dialog().getByText(/新增 1，重复 0/).waitFor();
    if(parts.join(',')!=='0,1,1,1,1,2')throw Error('Resume retransmitted an acknowledged part');
    checks.push('persistent failure stops after two retries and manual resume skips acknowledged parts');

    stage='lost response';await choose('lost-response');await dialog().getByText(/新增 1，重复 0/).waitFor();
    if(parts.join(',')!=='0,1,1,2'||job.uploaded_bytes!==600||completions!==1)throw Error('A lost response duplicated accepted data');
    checks.push('retry after a lost response keeps idempotent part semantics');

    stage='HTTP 503';await choose('unavailable');await dialog().getByText(/新增 1，重复 0/).waitFor();
    if(parts.join(',')!=='0,1,1,2')throw Error('Temporary HTTP failure was not recovered');
    checks.push('temporary HTTP 503 retries and recovers');

    stage='HTTP 422';await choose('invalid');await dialog().getByText('Request failed with status code 422',{exact:true}).waitFor();
    if(parts.join(',')!=='0,1'||completions)throw Error('Validation failure must not retry');
    checks.push('HTTP 422 validation errors stop immediately');

    stage='pause during backoff';await choose('pause');await dialog().getByText(/连接中断.*重试/).waitFor();
    await dialog().getByRole('button',{name:'暂停上传',exact:true}).click();
    await dialog().getByText(/已暂停传输/).waitFor({timeout:800});
    await page.waitForTimeout(1200);
    if(parts.join(',')!=='0,1'||completions)throw Error('Paused backoff submitted another request');
    checks.push('pause cancels retry delay immediately without another PUT');
    await page.screenshot({path:'output/playwright/upload-network-retry.png'});
    if(errors.length)throw Error('Uncaught browser errors: '+errors.join('; '));
    return {passed:true,checks,intercepted_writes:writes.length,business_writes:0};
  }catch(error){await page.screenshot({path:'output/playwright/upload-network-retry-failure.png'});throw Error(stage+': '+error.message);}
  finally{await page.goto('about:blank');await page.unrouteAll();page.off('pageerror',recordError);}
}
