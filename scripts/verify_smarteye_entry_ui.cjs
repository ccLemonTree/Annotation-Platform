async page=>{
  const base=page.url().startsWith('http')?new URL(page.url()).origin:'http://127.0.0.1:3000';
  const projectId='11111111-1111-4111-8111-111111111111';
  const api=url=>{const value=new URL(url);return value.origin===base&&value.pathname.startsWith('/api/');},writes=[];
  page.setDefaultTimeout(10000);
  await page.setViewportSize({width:1440,height:1000});
  await page.addInitScript(()=>{if(location.protocol==='http:')localStorage.setItem('token','isolated-entry-token');});
  await page.route(api,async route=>{
    const request=route.request(),path=new URL(request.url()).pathname;
    if(request.method()!=='GET'){
      writes.push(path);return route.fulfill({status:403,json:{detail:'Entry verification forbids mutations'}});
    }
    if(path==='/api/auth/me')return route.fulfill({json:{id:1,username:'fixture',role:'admin',tenant_id:1,is_active:true}});
    if(path==='/api/projects')return route.fulfill({json:{items:[{id:projectId,project_name:'入口隔离项目',task_type:'detection',total_assets:0,reviewed_count:0}],total:1}});
    if(path==='/api/stats/dashboard')return route.fulfill({json:{assets_overview:{total:0,project_references:0},annotations_overview:{total:0,approved:0,pending_review:0}}});
    if(path==='/api/images')return route.fulfill({json:{items:[],total:0,page:1,page_size:4}});
    if(path==='/api/images/collections'||path==='/api/model-services')return route.fulfill({json:[]});
    if(path==='/api/images/ingest-settings')return route.fulfill({json:{classification_enabled:false,min_long_edge:0,scenes:[]}});
    if(path.startsWith('/api/smarteye/')){
      const data=path.endsWith('/devices')?{items:[],total:0}:path.endsWith('/device-scenes/config')?{scenes:[],classification_enabled:false}:[];
      return route.fulfill({json:{code:0,data}});
    }
    // The deliberately injected main app may read its dashboard; never contact a backend.
    return route.fulfill({json:{}});
  });
  const inspect=async(entry)=>{
    const owner=page.locator('iframe[title="SmartEye 数据拉取"]');
    await owner.waitFor({state:'visible'});
    if(await owner.count()!==1)throw Error('Expected exactly one SmartEye frame');
    const handle=await owner.elementHandle(),frame=await handle.contentFrame();
    try{await frame.waitForFunction(()=>!!document.querySelector('.smarteye-app,.platform-shell'));}
    catch{throw Error('Frame app did not mount: '+JSON.stringify({entry,...await frame.evaluate(()=>({path:location.pathname,title:document.title,body:document.body.innerText.slice(0,160)}))}));}
    return frame.evaluate(()=>({
      path:location.pathname,projectId:new URLSearchParams(location.search).get('project_id'),title:document.title,
      smartEyeRoots:document.querySelectorAll('.smarteye-app').length,
      smartEyeNavigation:document.querySelectorAll('[aria-label="SmartEye 功能导航"]').length,
      mainAppShells:document.querySelectorAll('.platform-shell').length,
      mainNavigation:document.querySelectorAll('[aria-label="平台主导航"]').length,
      nestedFrames:document.querySelectorAll('iframe').length,
    }));
  };
  const results=[];
  const frameDocument=url=>{const value=new URL(url);return value.origin===base&&value.pathname==='/smarteye/index.html';};
  const scripts=html=>[...html.matchAll(/<script\b[^>]*\bsrc=["']([^"']+)["'][^>]*>/g)].map(match=>match[1]);
  try{
    const rootResponse=await page.request.get(base+'/index.html',{headers:{Accept:'text/html'}});
    const mainHtml=await rootResponse.text(),mainScripts=scripts(mainHtml),documents=[];
    for(const path of ['/smarteye','/smarteye?project_id='+projectId,'/smarteye/','/smarteye/?project_id='+projectId]){
      const response=await page.request.get(base+path,{headers:{Accept:'text/html'}});
      const html=await response.text(),contentType=response.headers()['content-type']||'';
      if(!response.ok()||!contentType.includes('text/html')||!/<html\b/i.test(html)||JSON.stringify(scripts(html))!==JSON.stringify(mainScripts))
        throw Error('Document entry did not serve main HTML: '+JSON.stringify({path,status:response.status(),contentType,scripts:scripts(html)}));
      documents.push({path,status:response.status(),contentType});
    }
    const embeddedResponse=await page.request.get(base+'/smarteye/index.html',{headers:{Accept:'text/html'}});
    const embeddedHtml=await embeddedResponse.text();
    if(!embeddedResponse.ok()||!embeddedHtml.includes('数据拉取 · 图像数据平台')||JSON.stringify(scripts(embeddedHtml))===JSON.stringify(mainScripts))
      throw Error('Explicit SmartEye HTML was rewritten to the main app');
    let moduleChecked=false;
    if(mainHtml.includes('/src/main.tsx')){
      const module=await page.request.get(base+'/smarteye/index.tsx',{headers:{Accept:'text/javascript'}});
      if(!module.ok()||!(module.headers()['content-type']||'').includes('javascript')||/<html\b/i.test(await module.text()))
        throw Error('Explicit SmartEye TSX module stopped serving JavaScript');
      const bareModule=await page.request.get(base+'/smarteye',{headers:{Accept:'text/javascript'}});
      if(!bareModule.ok()||!(bareModule.headers()['content-type']||'').includes('javascript'))
        throw Error('Non-document SmartEye module request was rewritten');
      moduleChecked=true;
    }
    for(const path of ['/detection-query','/smarteye','/smarteye?project_id='+projectId,'/smarteye/?project_id='+projectId]){
      await page.goto(base+path);
      await page.locator('.page-heading h1').getByText('数据拉取',{exact:true}).waitFor();
      const state=await inspect(path);
      const expectedProject=path.includes('?')?projectId:null;
      if(state.path!=='/smarteye/index.html'||state.projectId!==expectedProject||state.smartEyeRoots!==1||state.smartEyeNavigation!==1||state.mainAppShells||state.mainNavigation||state.nestedFrames)
        throw Error('SmartEye iframe loaded wrong app: '+JSON.stringify({entry:path,...state}));
      results.push({entry:path,...state});
    }
    await page.route(frameDocument,route=>route.fulfill({contentType:'text/html',body:mainHtml}));
    await page.goto(base+'/detection-query');
    const wrong=await inspect('injected-main-html');
    if(!wrong.mainAppShells||!wrong.mainNavigation||wrong.smartEyeRoots)throw Error('Injected SPA fallback did not reproduce the main app in the iframe: '+JSON.stringify(wrong));
    if(writes.length)throw Error('Entry page attempted mutation: '+writes.join(','));
    return {checks:['HTML Accept serves main app for bare/trailing-slash document entries','Explicit SmartEye HTML and dev TSX module remain separate','Both main-app entry aliases embed SmartEye, not the main app','Project query survives iframe entry','No second main sidebar or nested iframe','Real main HTML fallback is detected as wrong app'],base,documents,moduleChecked,entries:results,simulatedFallback:wrong,scope:'Local Vite dev/preview entry only; every API intercepted; no production route or deployment claimed'};
  }finally{
    await page.goto('about:blank');await page.unroute(frameDocument);await page.unroute(api);
  }
}
