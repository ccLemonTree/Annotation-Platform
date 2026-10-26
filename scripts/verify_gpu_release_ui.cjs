async page=>{
  await page.unrouteAll();page.setDefaultTimeout(12000);
  const origin='http://192.168.1.90:3000',pattern=/^http:\/\/192\.168\.1\.90:3000\/api\//;
  const id='11111111-1111-4111-8111-111111111111',events=[],errors=[];let stopped=false,fail=false;
  page.on('pageerror',error=>errors.push(error.message));
  await page.route(pattern,async route=>{
    const request=route.request(),path=new URL(request.url()).pathname,json=(body,status=200)=>route.fulfill({json:body,status});
    if(path==='/api/auth/me')return json({id:1,username:'fixture',role:'admin',is_active:true});
    if(path==='/api/gpu-resources/servers')return json([{id:'default',name:'Fixture server',enabled:true,revision:0,source:'ssh_manager'}]);
    if(path==='/api/gpu-resources')return json({server_name:'Fixture server',collected_at:new Date().toISOString(),cached:false,processes_complete:true,warnings:[],
      gpus:[{index:0,uuid:'GPU-aaa',name:'Fixture GPU',memory_total_mib:16000,memory_used_mib:stopped?0:8000,memory_free_mib:stopped?16000:8000,mig_mode:'Disabled'}],
      processes:stopped?[]:[{gpu_uuid:'GPU-aaa',pid:42,name:'fixture-python',memory_used_mib:8000}]});
    if(path.endsWith('/release/tasks'))return json(stopped?[]:[{kind:'training',id,name:'Fixture training',gpu_uuids:['GPU-aaa'],status:'running'}]);
    if(path.endsWith('/release/preview')){
      const input=request.postDataJSON();events.push({action:'preview',input});
      return json({token:'fixture-token',server_name:'Fixture server',target:input.pid?{kind:'process',pid:42,name:'fixture-python',gpu_uuids:['GPU-aaa'],container:null}:{kind:'training',id,name:'Fixture training',gpu_uuids:['GPU-aaa']}});
    }
    if(path.endsWith('/release/confirm')){
      const input=request.postDataJSON();events.push({action:'confirm',input});
      if(fail)return json({detail:'远端停止尚未确认，资源预留保留'},409);
      stopped=true;return json({stopped:true,message:'所选进程已退出，请查看刷新后的GPU占用'});
    }
    throw Error('Unexpected request '+request.method()+' '+path);
  });
  try{
    await page.goto(origin+'/gpu-resources');
    await page.getByRole('button',{name:'释放进程 42',exact:true}).click();
    let dialog=page.getByRole('dialog',{name:'确认释放GPU资源',exact:true});
    await dialog.getByText('fixture-python',{exact:true}).waitFor();
    if(events.some(event=>event.action==='confirm'))throw Error('Stopped before confirmation');
    await dialog.getByRole('button',{name:/取\s*消/}).click();
    if(events.some(event=>event.action==='confirm'))throw Error('Cancel stopped the process');
    await page.getByRole('button',{name:'释放任务 Fixture training',exact:true}).click();
    dialog=page.getByRole('dialog',{name:'确认释放GPU资源',exact:true});
    fail=true;await dialog.getByRole('button',{name:'确认停止并释放',exact:true}).click();
    await dialog.getByText('远端停止尚未确认，资源预留保留',{exact:true}).waitFor();
    await dialog.getByRole('button',{name:/取\s*消/}).click();
    fail=false;await page.getByRole('button',{name:'释放进程 42',exact:true}).click();
    dialog=page.getByRole('dialog',{name:'确认释放GPU资源',exact:true});
    await dialog.getByRole('checkbox',{name:'正常结束超时后强制结束进程',exact:true}).check();
    await dialog.getByRole('button',{name:'确认停止并释放',exact:true}).click();
    await page.getByText('所选进程已退出，请查看刷新后的GPU占用',{exact:true}).waitFor();
    await page.getByText('无计算进程（本次采集）',{exact:true}).waitFor();
    if(events.at(-1).input.token!=='fixture-token'||events.at(-1).input.force!==true||errors.length)throw Error(JSON.stringify({events,errors}));
    await page.setViewportSize({width:390,height:844});
    if(await page.locator('html').evaluate(el=>el.scrollWidth>el.clientWidth+1))throw Error('GPU page overflows narrow screen');
    await page.screenshot({path:'output/playwright/gpu-release-mobile.png',fullPage:true});
    return {checks:['Preview and cancel never stop a target','Task stopping failure remains visible and preserves reservation','Explicit process force option and signed confirmation submitted','Resource and task tables refresh after release','Narrow viewport fits'],scope:'Real LAN UI; all remote inspection and stop responses intercepted, no business workloads stopped'};
  }finally{await page.goto('about:blank');await page.unroute(pattern);}
}
