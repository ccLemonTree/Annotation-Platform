async page=>{
  await page.unrouteAll();page.setDefaultTimeout(10000);await page.setViewportSize({width:1440,height:1000});
  const origin='http://127.0.0.1:3000',checks=[],requests=[],errors=[];
  await page.addInitScript(()=>{
    if(location.origin!=='http://127.0.0.1:3000')return;
    localStorage.setItem('token','isolated-monitoring-fixture');localStorage.setItem('user',JSON.stringify({id:1,tenant_id:1,username:'fixture',role:'admin'}));
    const Native=window.WebSocket;window.terminalFixtureMessages=[];
    function Socket(url,protocols){
      if(!String(url).includes('/api/gpu-terminal/ws'))return protocols?new Native(url,protocols):new Native(url);
      this.readyState=0;this.bufferedAmount=0;
      setTimeout(()=>{this.readyState=1;this.onopen?.({});},10);
    }
    Socket.OPEN=1;Socket.CLOSED=3;
    Socket.prototype.send=function(value){if(typeof value==='string'){const parsed=JSON.parse(value);window.terminalFixtureMessages.push(parsed);if(parsed.type==='auth')queueMicrotask(()=>this.onmessage?.({data:JSON.stringify({type:'ready',server_name:parsed.ssh_server})}));}};
    Socket.prototype.close=function(){this.readyState=3;queueMicrotask(()=>this.onclose?.({}));};window.WebSocket=Socket;
  });
  const now=new Date().toISOString(),target={id:'target-one',name:'绍兴推理服务',kind:'model',target:{control_server:'control-one',file_server:'files-one',environment:'docker',runtime:'triton_plan',containers:['triton'],model_name:'detector',repository:'/models',runtime_config:{}},current:{state:'unmanaged'}};
  const record={id:'record-one',target_id:target.id,kind:'model',status:'staged',source:{name:'Detector',version:'v1'},target:target.target,inspection:{},logs:[],created_at:now};
  let failure=false;const reads={updates:0,service:0};
  const error=error=>errors.push(error.message);page.on('pageerror',error);
  await page.route(origin+'/api/**',async route=>{
    const req=route.request(),url=new URL(req.url()),path=url.pathname;
    requests.push({path,method:req.method()});if(req.method()!=='GET')throw Error('Unexpected business write '+path);
    if(path==='/api/auth/me')return route.fulfill({json:{id:1,tenant_id:1,username:'fixture',role:'admin',is_active:true,created_at:now}});
    if(path==='/api/model-deployments/servers')return route.fulfill({json:[{name:'control-one',host:'10.0.0.1',user:'root',port:22},{name:'files-one',host:'10.0.0.2',user:'root',port:22}]});
    if(path.endsWith('/logs')){
      const source=url.searchParams.get('source')||'updates';reads[source]++;
      if(failure)return route.fulfill({status:503,json:{detail:'fixture SSH unavailable'}});
      return route.fulfill({json:{content:source==='updates'?'pull layer '+reads.updates:'runtime '+(url.searchParams.get('instance')||'triton-a'),source,server:'control-one',status:'staged',truncated:source==='service',collected_at:now,instances:source==='service'?[{name:'triton-a',uid:'a'},{name:'triton-b',uid:'b'}]:[],selected_instance:source==='service'?url.searchParams.get('instance')||'triton-a':null}});
    }
    if(path==='/api/model-deployments/targets')return route.fulfill({json:{items:[target]}});
    if(path==='/api/model-deployments/sources'||path==='/api/model-deployments/releases')return route.fulfill({json:{items:[],has_more:false}});
    if(path==='/api/model-deployments')return route.fulfill({json:{items:[record],has_more:false}});
    if(path==='/api/model-deployments/record-one')return route.fulfill({json:record});
    if(path==='/api/model-services')return route.fulfill({json:[]});
    return route.fulfill({json:{}});
  });
  try{
    await page.goto(origin+'/model-deployments');await page.getByRole('link',{name:'SSH调试服务器',exact:true}).waitFor();
    await page.getByRole('tab',{name:'服务器版本与批量下发',exact:true}).click();
    const row=page.getByRole('row').filter({hasText:target.name});
    for(const name of ['control-one','files-one']){
      const href=await row.getByRole('link',{name:'SSH调试：'+name,exact:true}).getAttribute('href');
      if(href!=='/gpu-terminal?ssh_server='+name)throw Error('SSH link lost target');
    }
    checks.push('target row exposes distinct control/file SSH links and general server selector');
    await row.getByRole('button',{name:'实时日志',exact:true}).click();
    const dialog=page.getByRole('dialog'),output=dialog.getByLabel('服务器日志内容');
    await output.filter({hasText:'pull layer 1'}).waitFor();
    await page.waitForFunction(()=>document.querySelector('[aria-label="服务器日志内容"]')?.textContent.includes('pull layer 2'));
    checks.push('target update logs automatically refresh without business actions');
    await dialog.getByRole('checkbox',{name:'自动刷新（2秒）',exact:true}).uncheck();
    const paused=reads.updates;await new Promise(resolve=>setTimeout(resolve,2300));
    if(reads.updates!==paused)throw Error('Paused logs kept polling');
    failure=true;const previous=await output.textContent();await dialog.getByRole('button',{name:/刷新日志/}).click();
    await dialog.getByText('日志读取失败',{exact:true}).waitFor();if(await output.textContent()!==previous)throw Error('Failed refresh discarded previous logs');
    failure=false;await dialog.getByRole('button',{name:/刷新日志/}).click();await dialog.getByText('日志读取失败',{exact:true}).waitFor({state:'hidden'});
    checks.push('pause stops polling; manual failure keeps last content and retry clears error');
    await dialog.getByRole('combobox',{name:'日志来源'}).click();await page.locator('.ant-select-dropdown:visible').getByText('服务运行日志',{exact:true}).click();
    await output.filter({hasText:'runtime triton-a'}).waitFor();
    await dialog.getByRole('combobox',{name:'日志实例'}).click();await page.locator('.ant-select-dropdown:visible').getByText('triton-b',{exact:true}).click();
    await output.filter({hasText:'runtime triton-b'}).waitFor();await dialog.getByText('仅显示最近日志（最多64KiB）',{exact:true}).waitFor();
    checks.push('runtime source and instance selection are scoped; truncated snapshots are labeled');
    await page.screenshot({path:'output/playwright/deployment-monitoring-desktop.png'});
    await page.setViewportSize({width:390,height:844});
    await page.waitForFunction(()=>document.documentElement.scrollWidth<=innerWidth+1,{timeout:5000});
    await page.screenshot({path:'output/playwright/deployment-monitoring-mobile.png'});
    if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1))throw Error('Mobile page overflows '+JSON.stringify(await page.evaluate(()=>[...document.querySelectorAll('body *')].filter(e=>e.getBoundingClientRect().right>innerWidth+1||e.getBoundingClientRect().left< -1).slice(0,10).map(e=>({tag:e.tagName,class:e.className,width:e.getBoundingClientRect().width,left:e.getBoundingClientRect().left,right:e.getBoundingClientRect().right})))));
    await page.setViewportSize({width:1440,height:1000});
    await dialog.getByRole('checkbox',{name:'自动刷新（2秒）',exact:true}).check();await output.filter({hasText:'runtime triton-b'}).waitFor();
    await dialog.getByRole('button',{name:/Close|关闭/}).click();await dialog.waitFor({state:'hidden'});
    const closed=reads.service;await new Promise(resolve=>setTimeout(resolve,2300));if(reads.service!==closed)throw Error('Closed drawer kept polling');
    checks.push('mobile layout fits; closing the log drawer cancels polling');
    await page.getByRole('tab',{name:'下发与镜像记录',exact:true}).click();await page.getByRole('button',{name:'查看详情',exact:true}).click();
    await page.getByRole('dialog').getByLabel('服务器日志内容').waitFor();
    if(!requests.some(r=>r.path==='/api/model-deployments/record-one/logs'))throw Error('Record detail did not load scoped logs');
    checks.push('historical record details use their own log endpoint');
    await page.goto(origin+'/gpu-terminal?ssh_server=files-one');await page.getByRole('combobox',{name:'SSH调试服务器'}).waitFor();
    if((await page.getByRole('combobox',{name:'SSH调试服务器'}).locator('xpath=..').textContent()).includes('control-one'))throw Error('Wrong server selected');
    if(await page.evaluate(()=>window.terminalFixtureMessages.length))throw Error('Terminal connected automatically');
    await page.getByRole('button',{name:'连接终端',exact:true}).click();await page.getByText('已连接',{exact:true}).waitFor();
    const sent=await page.evaluate(()=>window.terminalFixtureMessages.find(r=>r.type==='auth'));
    if(sent.ssh_server!=='files-one'||sent.server_id)throw Error('Terminal did not authenticate named server');
    await page.getByRole('button',{name:'断开连接',exact:true}).click();
    checks.push('SSH entry selects the requested inventory server; connects only after a click');
    if(errors.length)throw Error('Browser errors: '+errors.join('; '));
    return {passed:true,checks,reads,business_writes:requests.filter(r=>r.method!=='GET').length,ssh:'mocked WebSocket; no remote commands'};
  }catch(cause){await page.screenshot({path:'output/playwright/deployment-monitoring-failure.png'});throw Error(JSON.stringify({checks,error:String(cause),url:page.url(),layout:await page.evaluate(()=>({scrollX,innerWidth,documentWidth:document.documentElement.scrollWidth,drawers:[...document.querySelectorAll('.ant-drawer,.ant-drawer-content-wrapper,.ant-drawer-section')].map(e=>({class:e.className,style:e.getAttribute('style'),left:e.getBoundingClientRect().left,width:e.getBoundingClientRect().width,maxWidth:getComputedStyle(e).maxWidth}))}))}));}
  finally{await page.goto('about:blank');await page.unrouteAll();page.off('pageerror',error);}
}
