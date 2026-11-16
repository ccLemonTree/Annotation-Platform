async page=>{
  await page.unrouteAll({behavior:'ignoreErrors'});
  const origin='http://127.0.0.1:3000',reads=[],writes=[],errors=[];
  const project={id:'11111111-1111-4111-8111-111111111111',project_name:'多轮目标标注',task_type:'caption',status:'active',label_schema:{labels:[]},total_assets:12,annotated_count:8,reviewed_count:6,created_at:'2026-11-04T00:00:00Z'};
  const device={plat_id:'p',device_id:'d',device_name:'学校摄像头',platform_name:'测试平台',scene_labels:['学校'],scene_status:'completed',representative_url:'/api/smarteye/devices/p/d/preview'};
  const model={id:'model-1',name:'人脸检测模型',version:'v1',format:'onnx',files:[{path:'model.onnx',bytes:1024,sha256:'a'.repeat(64)}],entry_file:'model.onnx',file_size:1024,status:'ready',source:{kind:'upload'},created_at:'2026-11-04T00:00:00Z'};
  page.on('pageerror',error=>errors.push(error.message));
  await page.addInitScript(()=>localStorage.setItem('token','isolated-project-first'));
  const handler=async route=>{
    const request=route.request(),url=new URL(request.url()),p=url.pathname;
    reads.push(p+url.search);
    if(request.method()!=='GET')writes.push({path:p,body:request.postDataJSON()});
    let data;
    if(p==='/api/auth/me')data={id:1,username:'隔离验收',role:'admin',tenant_id:1};
    else if(p==='/api/projects')data={items:[project],total:1};
    else if(p==='/api/projects/'+project.id)data=project;
    else if(p==='/api/images/collections')data=[];
    else if(p==='/api/assets')return route.fulfill({json:{items:[],total:0,page:1,page_size:20},headers:{'X-Asset-Crop-Depth-Filter':'1'}});
    else if(p==='/api/model-services')data=[];
    else if(p.endsWith('/work/jobs'))data=[];
    else if(p==='/api/model-repository/groups')data={items:[{name:'人脸模型',version_count:2,formats:['onnx'],gpu_models:[],latest_at:model.created_at,importing_count:0}],total:1,version_total:2,gpu_models:[]};
    else if(p==='/api/model-repository')data={items:[model,{...model,id:'model-2',version:'v2',status:'failed',error:'隔离失败原因'}],total:2};
    else if(p==='/api/smarteye/devices/p/d/preview')return route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720"><rect width="1280" height="720" fill="#315246"/><text x="400" y="360" fill="white" font-size="64">代表图</text></svg>'});
    else if(p.startsWith('/api/smarteye/')){
      if(p.endsWith('/managed-platforms'))data=[{plat_id:'p',platform_name:'测试平台',state:'1',connection_status:'online'}];
      else if(p.endsWith('/device-scenes/config'))data={scenes:['学校'],classification_enabled:true};
      else if(p.endsWith('/devices'))data={items:[device],total:1};
      else if(p.endsWith('/scene'))data=device;
      else if(p.endsWith('/tasks')||p.endsWith('/device-capture/schedules'))data=[];
      else throw Error('未覆盖接口 '+p);
      return route.fulfill({json:{code:0,data}});
    }else throw Error('未覆盖接口 '+p);
    return route.fulfill({json:data});
  };
  const apiPattern=/^https?:\/\/[^/]+\/api\//;
  await page.route(apiPattern,handler);
  try{
    await page.setViewportSize({width:1440,height:1000});
    await page.goto(origin+'/projects');
    const card=page.getByRole('article',{name:project.project_name,exact:true});await card.waitFor();
    if(await page.locator('#platform-navigation').getByText('数据拉取',{exact:true}).count())throw Error('仍有独立采集导航');
    await page.screenshot({path:'output/playwright/project-first-cards.png',fullPage:true});
    await card.getByRole('button',{name:'定时抓图 / 数据拉取'}).click();
    const frame=page.frameLocator('iframe[title="SmartEye 数据拉取"]');
    if(!await page.locator('iframe').getAttribute('src').then(value=>value.includes(project.id)))throw Error('采集未固定项目');
    await frame.getByRole('button',{name:'修正场景',exact:true}).click();
    const dialog=frame.getByRole('dialog',{name:'人工修正设备场景'});await dialog.waitFor();
    const image=dialog.getByRole('img',{name:device.device_name+' 代表图'});
    await image.waitFor();await image.evaluate(image=>image.decode());
    await image.evaluate(image=>new Promise((resolve,reject)=>{
      const started=Date.now();const check=()=>image.getBoundingClientRect().width>=600?resolve():Date.now()-started>3000?reject(Error('修正弹窗没有放大代表图')):requestAnimationFrame(check);check();
    }));
    await page.screenshot({path:'output/playwright/project-first-scene.png',fullPage:true});
    await dialog.getByRole('button',{name:'保存场景'}).click();await dialog.waitFor({state:'hidden'});
    if(!writes.some(write=>write.path.endsWith('/devices/p/d/scene')))throw Error('场景未保存');
    await page.locator('.ant-drawer-close').click();
    await page.goto(origin+'/projects/'+project.id+'/prepare');
    const depth=page.getByRole('spinbutton',{name:'切图轮次'});
    const filtered=page.waitForResponse(response=>response.url().includes('/api/assets?')&&new URL(response.url()).searchParams.get('crop_depth')==='2');
    await depth.fill('2');await depth.blur();await filtered;
    await page.goto(origin+'/model-repository');
    await page.getByRole('article',{name:'人脸模型',exact:true}).getByRole('button',{name:'查看模型版本'}).click();
    const version=page.getByRole('article',{name:model.name+' v1',exact:true});await version.waitFor();
    await version.getByRole('checkbox').check();
    if(!await page.getByRole('article',{name:model.name+' v2',exact:true}).getByRole('checkbox').isDisabled())throw Error('失败版本可下载');
    await version.getByRole('button',{name:model.name,exact:true}).click();
    await page.getByRole('dialog',{name:'版本详情'}).getByText('model.onnx',{exact:true}).first().waitFor();
    await page.getByRole('dialog',{name:'版本详情'}).getByRole('button',{name:'关闭',exact:true}).last().click();
    await page.getByRole('dialog',{name:'版本详情'}).waitFor({state:'hidden'});
    await page.screenshot({path:'output/playwright/project-first-models.png',fullPage:true});
    await page.setViewportSize({width:390,height:844});
    if(await page.locator('html').evaluate(element=>element.scrollWidth>element.clientWidth+1))throw Error('手机布局溢出');
    if(errors.length)throw Error(errors.join(';'));
    return {checks:['项目卡片与操作入口','移除独立数据流程导航','采集固定项目','修正场景自动放大代表图','保存场景','按轮次筛选切图','模型组与版本卡片','跨版本勾选与失败禁用','模型详情','390px布局'],writes};
  }finally{await page.goto('about:blank');await page.unroute(apiPattern,handler);await page.setViewportSize({width:1440,height:1000});}
}
