async page=>{
 const base='http://127.0.0.1:3000',api=base+'/api/**',reads=[],writes=[];
 const devices=Array.from({length:1101},(_,i)=>({plat_id:i%2?'a':'b',platform_name:i%2?'平台A':'平台B',device_id:String(Math.floor(i/2)),device_name:`摄像头${i}`,scene_labels:[i<205?'工厂':'道路'],scene_status:'completed',scene_source:'auto'}));
 let mode='normal',releasePage2;
 await page.addInitScript(()=>{if(location.protocol.startsWith('http'))localStorage.setItem('token','isolated-device-select-all');});
 await page.route(api,async route=>{
  const req=route.request(),url=new URL(req.url()),path=url.pathname,p=url.searchParams;
  if(req.method()!=='GET')writes.push({path,body:req.postDataJSON()});
  if(path==='/api/projects')return route.fulfill({json:{items:[],total:0}});
  if(path==='/api/images/collections')return route.fulfill({json:[]});
  let data=[];
  if(path.endsWith('/managed-platforms'))data=[{plat_id:'a',platform_name:'平台A',state:'1',connection_status:'online'},{plat_id:'b',platform_name:'平台B',state:'1',connection_status:'online'}];
  else if(path.endsWith('/device-scenes/config'))data={scenes:['工厂','道路'],scene_groups:[{name:'生产作业',scenes:['工厂']},{name:'交通场景',scenes:['道路']}],classification_enabled:true};
  else if(path.endsWith('/devices')){
   const current=Number(p.get('page')),size=Number(p.get('page_size'));reads.push({page:current,size,scene:p.getAll('scene'),q:p.get('q'),platform:p.get('platform_id')});
   const scene=p.getAll('scene').filter(Boolean);
   const matched=devices.filter(d=>(!p.get('platform_id')||d.plat_id===p.get('platform_id'))&&(!p.get('q')||d.device_name.includes(p.get('q')))&&(!scene.length||d.scene_labels.some(label=>scene.includes(label))));
   if(size===200&&current===2){
    if(mode==='fail')return route.fulfill({status:500,json:{code:1,message:'第二页读取失败'}});
    if(mode==='delay')await new Promise(resolve=>{releasePage2=resolve;});
   }
   data={items:matched.slice((current-1)*size,current*size),total:matched.length+(mode==='drift'&&size===200&&current===2?1:0)};
  }else if(path.endsWith('/devices/classify'))data={task_id:'fixture'};
  else if(!path.endsWith('/tasks')&&!path.endsWith('/device-capture/schedules'))throw Error('Unmocked API '+path);
  return route.fulfill({json:{code:0,data}});
 });
 const count=async n=>page.getByText(`已选 ${n} 台设备`,{exact:true}).waitFor();
 const all=()=>page.locator('button').filter({hasText:'全选筛选结果'}).click();
 const firstCheckbox=()=>page.locator('#device-list .ant-table-tbody tr.ant-table-row').first().getByRole('checkbox');
 const search=async text=>{const input=page.getByLabel('搜索设备',{exact:true});await input.fill(text);await input.press('Enter');await page.waitForTimeout(150);};
 try{
  await page.setViewportSize({width:1440,height:1100});await page.goto(base+'/smarteye/index.html?project_id=fixture');
  await page.getByRole('heading',{name:'设备抓图与场景',exact:true}).waitFor();
  if(!await page.getByRole('button',{name:'全选筛选结果',exact:true}).count())throw Error('Missing select all filtered devices button');
  await all();await count(1101);
  const selectedReads=reads.filter(q=>q.size===200);if(selectedReads.length!==6||selectedReads.at(-1).page!==6)throw Error('Selection did not cover all pages');
  if(!await page.getByRole('button',{name:'分类所选设备',exact:true}).isDisabled()||!await page.getByRole('button',{name:'创建定时拉图',exact:true}).isDisabled())throw Error('Selected-task1000 limit silently bypassed');
  await firstCheckbox().uncheck();await count(1100);
  await page.getByTitle('2',{exact:true}).click();await count(1100);await firstCheckbox().uncheck();await count(1099);
  await page.getByRole('combobox',{name:'设备场景',exact:true}).click();await page.locator('.ant-select-dropdown:visible .ant-select-tree-title').getByText('工厂',{exact:true}).click();await page.keyboard.press('Escape');await count(0);
  await all();await count(205);
  await page.getByRole('button',{name:'分类所选设备',exact:true}).click();
  const classified=writes.find(x=>x.path.endsWith('/devices/classify'))?.body.devices;
  if(classified?.length!==205||new Set(classified.map(d=>JSON.stringify([d.plat_id,d.device_id]))).size!==205)throw Error('Selected references lost unseen pages or platform identity');
  await search('摄像头20');await count(0);await all();await count(6);
  if(!reads.some(q=>q.size===200&&q.q==='摄像头20'&&q.scene.includes('工厂')))throw Error('Combined filters not sent to selection');
  await search('');await count(0);await firstCheckbox().check();await count(1);
  mode='fail';await all();await page.getByText('第二页读取失败',{exact:true}).waitFor();await count(1);
  mode='drift';await all();await page.getByText('筛选结果在加载期间发生变化，未更新选择；请稍后重试',{exact:true}).waitFor();await count(1);
  mode='delay';await all();await page.getByText('正在选择 200 / 205 台',{exact:true}).waitFor();
  await page.getByRole('button',{name:'清空选择',exact:true}).click();await count(0);if(!releasePage2)throw Error('No delayed page');releasePage2();await page.waitForTimeout(250);await count(0);
  releasePage2=undefined;await all();await page.getByText('正在选择 200 / 205 台',{exact:true}).waitFor();
  await search('摄像头20');await count(0);releasePage2();await page.waitForTimeout(250);await count(0);
  mode='normal';await all();await count(6);
  await page.setViewportSize({width:390,height:844});await page.waitForTimeout(300);
  if(await page.locator('html').evaluate(el=>el.scrollWidth>el.clientWidth+1))throw Error('Mobile selection overflow');
  return {checks:['1101 devices across6 pages','platform and device composite identity','deselect unseen-page records safely','paging preserves selection','filters clear and constrain selection','1000 applies to existing task actions only','no partial application on page failure or changing totals','clear/filter change cancels late selection','390px layout'],scope:'all APIs mocked; no real classification or sync'};
 }catch(error){await page.screenshot({path:'output/playwright/device-select-all-failure.png',fullPage:true});throw error;}
 finally{if(releasePage2)releasePage2();await page.goto('about:blank');await page.unroute(api);}
}
