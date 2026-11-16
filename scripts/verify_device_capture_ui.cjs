async page => {
  const errors=[];const onError=error=>errors.push(error.message);page.on('pageerror',onError);
  const writes = [], devices = [{plat_id:'p1',device_id:'same',device_name:'一号设备',platform_name:'测试平台',scene_labels:['工厂'],scene_status:'completed',scene_source:'auto'},{plat_id:'p2',device_id:'same',device_name:'二号设备',platform_name:'第二平台',scene_labels:[],scene_status:'unclassified'}];
  const platform = {plat_id:'p1',platform_name:'测试平台',base_url:'http://fixture.test',username:'fixture',state:'1',has_password:true,connection_status:'online',device_count:1};
  let platforms=[platform];
  let schedules=[];
  const tasks=[{task_id:'sync-fixture',kind:'device-sync',status:'running',progress:50,message:'同步处理中',platform_results:[{plat_id:'p2',platform_name:'第二平台',status:'failed',error:'设备查询权限不足'}]}];
  await page.addInitScript(()=>{if(location.protocol==='http:'||location.protocol==='https:')localStorage.setItem('token','isolated-device-ui-token');});
  const apiPattern=/^https?:\/\/[^/]+\/api\//;
  await page.route(apiPattern,async route=>{
    const request=route.request(),url=new URL(request.url()),path=url.pathname,method=request.method();
    if(method!=='GET')writes.push({path,method,body:request.postDataJSON(),headers:await request.allHeaders()});
    let data;
    if(path==='/api/auth/me')return route.fulfill({json:{id:1,username:'fixture',role:'admin',tenant_id:1,is_active:true}});
    if(path==='/api/projects')return route.fulfill({json:{items:[{id:'project-fixture',project_name:'测试标注项目'}],total:1}});
    if(path==='/api/images/collections')return route.fulfill({json:[{id:'collection-fixture',name:'测试图片集'}]});
    if(path==='/api/smarteye/managed-platforms'){if(method==='POST')platforms.push({...request.postDataJSON(),has_password:true,connection_status:'unchecked'});data=method==='GET'?platforms:platforms.at(-1);}
    else if(path==='/api/smarteye/managed-platforms/check-all')data=null;
    else if(path.endsWith('/check'))data={...platform,connection_status:'error',connection_error:'连接超时'};
    else if(path.startsWith('/api/smarteye/managed-platforms/')){if(method==='DELETE')platforms=platforms.filter(item=>!path.endsWith('/'+item.plat_id));data=platform;}
    else if(path==='/api/smarteye/devices')data={items:devices,total:2};
    else if(path==='/api/smarteye/platform-list')data=[];
    else if(path==='/api/smarteye/device-scenes/config')data={scenes:['工厂','河边'],classification_enabled:true};
    else if(path==='/api/smarteye/device-capture/schedules'){
      if(method==='POST')schedules=[{...request.postDataJSON(),sched_id:'schedule-fixture',running:false}];
      data=method==='GET'?schedules:schedules[0];
    }else if(path.startsWith('/api/smarteye/device-capture/schedules/')){
      if(path.endsWith('/stop'))schedules[0].enabled=false;
      if(path.endsWith('/start'))schedules[0].enabled=true;
      if(method==='DELETE')schedules=[];
      data=schedules[0]||{};
    }else if(path==='/api/smarteye/tasks')data=tasks;
    else if(path.endsWith('/cancel')){tasks[0].status='cancelled';data={};}
    else if(path.endsWith('/scene'))data={scene_labels:request.postDataJSON().scene_labels};
    else if(path.startsWith('/api/smarteye/'))data={task_id:'task-fixture'};
    else return route.fulfill({json:[]});
    await route.fulfill({json:{code:0,data}});
  });
  try{
    await page.setViewportSize({width:1440,height:1000});
    await page.goto('http://localhost:3000/smarteye?project_id=project-fixture');
    const frame=page.frameLocator('iframe[title="SmartEye 数据拉取"]');
    await frame.getByRole('heading',{name:'设备抓图与场景'}).waitFor();
    await frame.locator('thead').first().getByRole('checkbox').check();
    await frame.getByRole('button',{name:'创建定时拉图'}).click();
    const modal=frame.getByRole('dialog',{name:'创建定时拉图'});
    await modal.getByRole('textbox',{name:'任务名称'}).fill('隔离定时任务');
    await modal.getByRole('button',{name:'创建任务'}).click();
    await modal.waitFor({state:'hidden'});
    const create=writes.find(write=>write.path.endsWith('/schedules'));
    if(create?.body.project_id!=='project-fixture'||create.body.devices.length!==2||create.body.devices[0].plat_id===create.body.devices[1].plat_id)throw Error('项目预选或设备复合标识丢失');
    const embedded=page.frames().find(frame=>frame.url().includes('/smarteye/index.html'));
    await embedded.evaluate(async()=>{const network=await import('/smarteye/network.ts');network.setDestination({purpose:'annotation',projectId:''});});
    await frame.getByRole('button',{name:/^暂\s*停$/}).click();
    await frame.getByRole('button',{name:/^启\s*用$/}).click();
    const start=writes.find(write=>write.path.endsWith('/schedule-fixture/start'));
    if(Object.keys(start.headers).some(header=>header.startsWith('x-smarteye-')))throw Error('旧页目的地请求头污染设备计划启用');
    await frame.getByRole('button',{name:'立即拉取'}).click();
    await frame.getByRole('button',{name:'分类所选设备'}).click();
    await frame.getByRole('button',{name:'全部设备分类'}).click();
    await frame.getByRole('button',{name:/^确\s*定$/}).click();
    if(!writes.some(write=>write.path.endsWith('/devices/classify')&&!write.body.devices&&Array.isArray(write.body.platform_ids)))throw Error('全部设备分类意外限制在已选设备');
    await frame.getByRole('button',{name:'修正场景'}).first().click();
    const scene=frame.getByRole('dialog',{name:'人工修正设备场景'});
    await scene.getByRole('button',{name:'保存场景'}).click();
    await scene.waitFor({state:'hidden'});
    await frame.getByText('完成 0 个平台，失败 1 个；同步 0 台设备',{exact:false}).waitFor();
    await frame.getByRole('button',{name:'查看明细',exact:true}).click();
    await frame.getByText('设备查询权限不足',{exact:true}).waitFor();
    await frame.getByText('任务编号：sync-fixture',{exact:false}).waitFor();
    await frame.getByRole('button',{name:'停止执行'}).click();
    if(!writes.some(write=>write.path==='/api/smarteye/tasks/sync-fixture/cancel'))throw Error('全局同步任务取消失败');
    await frame.getByRole('menuitem').filter({hasText:'平台管理'}).click();
    await frame.getByRole('button',{name:'连接测试'}).click();
    await frame.getByText('测试平台：连接超时',{exact:true}).waitFor();
    await frame.getByRole('button',{name:'编辑平台'}).click();
    const edit=frame.getByRole('dialog',{name:'编辑平台'});
    if(await edit.getByLabel('平台密码').inputValue())throw Error('密码被回显');
    await edit.getByRole('button',{name:'保存平台'}).click();
    const update=writes.find(write=>write.method==='PUT'&&write.path.includes('/managed-platforms/'));
    if(update?.body.password)throw Error('空密码覆盖已有密码');
    await frame.getByRole('button',{name:'新增平台'}).click();
    const add=frame.getByRole('dialog',{name:'新增平台'});
    await add.getByLabel('平台标识').fill('new-platform');
    await add.getByLabel('平台名称').fill('新平台');
    await add.getByLabel('服务地址').fill('http://new-fixture.test');
    await add.getByLabel('登录用户名').fill('fixture');
    await add.getByLabel('平台密码').fill('fixture-password');
    await add.getByRole('button',{name:'保存平台'}).click();
    await add.waitFor({state:'hidden'});
    await frame.getByRole('row').filter({has:frame.getByRole('cell',{name:'新平台',exact:true})}).getByRole('button',{name:'删除平台'}).click();
    await frame.getByRole('button',{name:/^确\s*定$/}).click();
    if(!writes.some(write=>write.method==='DELETE'&&write.path.endsWith('/new-platform')))throw Error('删除平台未提交');
    await page.setViewportSize({width:390,height:844});
    if(await frame.locator('html').evaluate(element=>element.scrollWidth>element.clientWidth+1))throw Error('平台管理移动端溢出');
    await page.screenshot({path:'output/playwright/platform-management-mobile.png',fullPage:true});
    await page.setViewportSize({width:1440,height:1000});
    await frame.getByRole('menuitem').filter({hasText:'设备抓图'}).click();
    await frame.getByRole('heading',{name:'设备抓图与场景'}).waitFor();
    await page.setViewportSize({width:390,height:844});
    if(await frame.locator('html').evaluate(element=>element.scrollWidth>element.clientWidth+1))throw Error('设备抓图移动端溢出');
    await page.screenshot({path:'output/playwright/device-capture-mobile.png',fullPage:true});
    await page.setViewportSize({width:1440,height:1000});
    await page.screenshot({path:'output/playwright/device-capture-desktop.png',fullPage:true});
    await frame.getByRole('menuitem').filter({hasText:'检测查询'}).click();
    await frame.getByText('直接用于标注',{exact:true}).waitFor();
    await frame.locator('html').evaluate(async()=>{
      const network=await import('/smarteye/network.ts');
      await network.apiFetch('/api/smarteye/download',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({platName:'fixture'})});
    });
    const projectPull=writes.find(write=>write.path==='/api/smarteye/download');
    if(projectPull?.headers['x-smarteye-project']!=='project-fixture'||projectPull.headers['x-smarteye-purpose']!=='annotation')throw Error('项目上下文未传递给旧页拉取');
    if(errors.length)throw Error(errors.join(';'));
    return {checks:['所有 API 均使用隔离响应','同设备ID跨平台独立选择','项目入口预选并持久绑定','批量分类与人工修正','平台连接失败可见、密码不回显'],writes};
  }catch(error){await page.screenshot({path:'output/playwright/device-management-failure.png',fullPage:true});throw error;
  }finally{page.off('pageerror',onError);await page.goto('about:blank');await page.unroute(apiPattern);await page.setViewportSize({width:1440,height:1000});}
}
