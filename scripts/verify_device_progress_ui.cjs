async page=>{
 const base='http://127.0.0.1:3000',api=base+'/api/**';
 let platforms=[
  {plat_id:'a',platform_name:'平台A',state:'1',connection_status:'online',device_count:10,classified_count:4,scene_unmatched_count:2,scene_failed_count:1,scene_pending_count:2},
  {plat_id:'b',platform_name:'平台B',state:'1',connection_status:'online',device_count:10,classified_count:2,scene_unmatched_count:1,scene_failed_count:0,scene_pending_count:7},
  {plat_id:'c',platform_name:'离线平台',state:'1',connection_status:'error',device_count:10,classified_count:10,scene_unmatched_count:0,scene_failed_count:0,scene_pending_count:0}];
 const tasks=[{task_id:'sync',kind:'device-sync',status:'running',progress:40,platform_results:[
  {plat_id:'a',platform_name:'平台A',status:'running',count:3,device_total:10,image_count:2,image_missing_count:1,image_failed_count:0},
  {plat_id:'b',platform_name:'平台B',status:'running',count:0},
  {plat_id:'c',platform_name:'离线平台',status:'completed',count:10,device_total:10}]},
  {task_id:'batch',kind:'device-scenes',status:'running',processed_count:6,failed_count:0,unmatched_count:1,skipped_count:2}];
 await page.addInitScript(()=>{if(location.protocol.startsWith('http'))localStorage.setItem('token','isolated-device-progress');});
 await page.route(api,route=>{
  const req=route.request(),path=new URL(req.url()).pathname;
  if(req.method()!=='GET')throw Error('Unexpected business write '+path);
  if(path==='/api/projects')return route.fulfill({json:{items:[],total:0}});
  if(path==='/api/images/collections')return route.fulfill({json:[]});
  let data=[];
  if(path.endsWith('/managed-platforms'))data=platforms;
  else if(path.endsWith('/device-scenes/config'))data={scenes:['工厂'],classification_enabled:true};
  else if(path.endsWith('/devices'))data={items:[],total:0};
  else if(path.endsWith('/tasks'))data=tasks;
  else if(!path.endsWith('/device-capture/schedules'))throw Error('Unmocked API '+path);
  return route.fulfill({json:{code:0,data}});
 });
 const openProgress=async()=>{const drawer=page.getByRole('dialog',{name:'同步与分类进度',exact:true});if(!await drawer.isVisible())await page.getByRole('button',{name:/^同步与分类进度/}).click();await drawer.waitFor();};
 const closeProgress=async()=>{const drawer=page.getByRole('dialog',{name:'同步与分类进度',exact:true});if(await drawer.isVisible()){await drawer.locator('.ant-drawer-close').click();await drawer.waitFor({state:'hidden'});}};
 const percent=async(name,value)=>{await openProgress();await page.getByRole('progressbar',{name,exact:true}).waitFor();await page.waitForFunction(({name,value})=>document.querySelector(`[aria-label="${name}"]`)?.getAttribute('aria-valuenow')===String(value),{name,value});};
 const refresh=async()=>{await closeProgress();await page.locator('button').filter({hasText:'刷新设备'}).click();await openProgress();};
 try{
  await page.setViewportSize({width:1440,height:1100});
  await page.goto(base+'/smarteye/index.html?project_id=fixture');
  await page.getByRole('heading',{name:'设备抓图与场景',exact:true}).waitFor();
  await openProgress();
  await percent('设备同步平台进度',40);
  await percent('平台A代表图进度',30);
  await page.getByText('平台B：正在登录或查询设备列表',{exact:true}).waitFor();
  await percent('场景分类总进度',66);
  await page.getByText('已处理分类 20 / 30 台（含未匹配和失败）',{exact:true}).waitFor();
  await page.getByText('当前分类批次已处理 6 台，跳过 2 台',{exact:true}).waitFor();
  await page.screenshot({path:'output/playwright/device-progress-desktop.png'});
  await page.setViewportSize({width:390,height:844});await page.waitForTimeout(300);
  if(await page.locator('html').evaluate(el=>el.scrollWidth>el.clientWidth+1))throw Error('Mobile active-progress overflow');
  await page.screenshot({path:'output/playwright/device-progress-mobile.png',fullPage:true});
  await page.setViewportSize({width:1440,height:1100});
  await closeProgress();await page.getByRole('checkbox',{name:'仅在线平台',exact:true}).check();await percent('场景分类总进度',50);
  await closeProgress();await page.getByRole('combobox',{name:'设备平台',exact:true}).click();
  await page.locator('.ant-select-dropdown:visible .ant-select-item-option-content').getByText('平台A（在线）',{exact:true}).click();await percent('场景分类总进度',70);
  platforms=platforms.map(p=>p.plat_id==='a'?{...p,device_count:20,scene_pending_count:12}:p);
  await refresh();await percent('场景分类总进度',35);
  tasks[0].status='completed';tasks[0].progress=100;tasks[1].status='completed';tasks[1].progress=100;
  platforms=platforms.map(p=>p.plat_id==='a'?{...p,device_count:20,classified_count:15,scene_unmatched_count:3,scene_failed_count:2,scene_pending_count:0}:p);
  await refresh();await percent('场景分类总进度',100);if(await page.locator('.ant-progress-status-exception').count())throw Error('已处理覆盖率不应因部分失败显示整条异常');
  await page.getByText('已处理分类 20 / 20 台（含未匹配和失败）',{exact:true}).waitFor();
  platforms=platforms.map(p=>({...p,device_count:0,classified_count:0,scene_unmatched_count:0,scene_failed_count:0,scene_pending_count:0}));
  await refresh();await page.getByText('当前范围暂无已同步设备',{exact:true}).waitFor();await percent('场景分类总进度',0);
  await page.setViewportSize({width:390,height:844});await page.waitForTimeout(300);
  if(await page.locator('html').evaluate(el=>el.scrollWidth>el.clientWidth+1))throw Error('Mobile progress overflow');
  return {checks:['sync platform percent','per-platform representative count and percent','unknown list-query stage has no fabricated total','classification coverage includes unmatched and failures','current batch separate from all-device progress','scope and growing denominator refresh','empty devices stays0','390px layout'],scope:'all APIs mocked; no business writes'};
 }catch(error){await page.screenshot({path:'output/playwright/device-progress-failure.png',fullPage:true});throw error;}
 finally{await page.goto('about:blank');await page.unroute(api);}
}
