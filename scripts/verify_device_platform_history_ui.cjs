async page=>{
 const queries=[];let platformReads=0;
 const api='http://127.0.0.1:3000/api/**';
 await page.addInitScript(()=>{if(location.protocol.startsWith('http'))localStorage.setItem('token','isolated-platform-history');});
 await page.route(api,async route=>{
  const url=new URL(route.request().url()),path=url.pathname;
  if(route.request().method()!=='GET')throw Error('Unexpected write '+path);
  if(path==='/api/projects')return route.fulfill({json:{items:[],total:0}});
  if(path==='/api/images/collections')return route.fulfill({json:[]});
  let data=[];
  if(path.endsWith('/managed-platforms')){platformReads++;data=[{plat_id:'a',platform_name:'测试平台',state:'1',connection_status:'online',device_count:20,classified_count:12,scene_failed_count:3,scene_pending_count:2,scene_unmatched_count:1}];}
  else if(path.endsWith('/device-scenes/config'))data={scenes:['工厂'],classification_enabled:true};
  else if(path.endsWith('/devices')){queries.push(Object.fromEntries(url.searchParams));data={items:[{plat_id:'a',device_id:'failed-1',device_name:'北门摄像头',platform_name:'测试平台',scene_status:'failed',scene_error:'设备抓图不可读取，请重新拉取后重试',scene_labels:[]}],total:1};}
  else if(path.endsWith('/tasks'))data=[{task_id:'scene-history',kind:'device-scenes',status:'failed',created_at:'2026-11-03T02:00:00Z',updated_at:'2026-11-03T02:01:00Z',processed_count:8,failed_count:1,unmatched_count:1,skipped_count:2,platform_results:[{plat_id:'a',platform_name:'测试平台',status:'failed',processed_count:8,success_count:6,failed_count:1,unmatched_count:1,skipped_count:2,device_results:[{plat_id:'a',device_id:'failed-1',device_name:'北门摄像头',status:'failed',error:'设备抓图不可读取，请重新拉取后重试'}]}]}];
  else if(!path.endsWith('/device-capture/schedules'))throw Error('Unmocked API '+path);
  return route.fulfill({json:{code:0,data}});
 });
 try{
  await page.setViewportSize({width:1440,height:1100});
  await page.goto('http://127.0.0.1:3000/smarteye/index.html?project_id=fixture');
  const overview=page.getByRole('row').filter({has:page.getByRole('button',{name:'查看失败设备',exact:true})});
  await overview.waitFor();
  const cells=await overview.getByRole('cell').allTextContents();
  if(JSON.stringify(cells.slice(2,9))!==JSON.stringify(['20','16','12','3','1','2','2']))throw Error('Wrong platform totals '+JSON.stringify(cells));
  const readsBefore=platformReads;
  await page.getByRole('button',{name:'刷新设备',exact:true}).click();await page.waitForTimeout(350);
  if(platformReads<=readsBefore)throw Error('Refresh did not refresh platform totals');
  await overview.getByRole('button',{name:'查看失败设备',exact:true}).click();
  await page.waitForTimeout(350);
  if(!queries.some(q=>q.platform_id==='a'&&q.scene_status==='failed'&&q.page==='1'&&!q.has_image))throw Error('Failure drilldown lost filters');
  await page.getByRole('button',{name:'查看明细',exact:true}).click();
  await page.getByText('已处理 8 台；成功 6 台；未匹配 1 台；失败 1 台；跳过 2 台',{exact:true}).waitFor();
  await page.locator('button.ant-table-row-expand-icon').click();
  await page.getByRole('columnheader',{name:'失败设备名称',exact:true}).waitFor();
  const detail=page.locator('.ant-table-expanded-row').last();
  await detail.getByText('北门摄像头',{exact:true}).waitFor();
  await detail.getByText('设备抓图不可读取，请重新拉取后重试',{exact:true}).waitFor();
  await page.screenshot({path:'output/playwright/device-platform-history.png',fullPage:true});
  await page.setViewportSize({width:390,height:844});
  await page.waitForTimeout(300);
  if(await page.locator('html').evaluate(el=>el.scrollWidth>el.clientWidth+1)){
   await page.screenshot({path:'output/playwright/device-platform-history-mobile-failure.png',fullPage:true});
   const overflow=await page.locator('section,table,.ant-table-wrapper,.ant-table-content').evaluateAll(nodes=>nodes.filter(n=>n.getBoundingClientRect().right>innerWidth+1).slice(0,8).map(n=>({tag:n.tagName,class:n.className,width:n.getBoundingClientRect().width,scroll:n.scrollWidth,client:n.clientWidth})));
   throw Error('Mobile page overflow '+JSON.stringify(overflow));
  }
  return {checks:['platform totals separate current success/failure/pending/unclassified','all failed devices accessible with paginated filter','per-run platform success/failure/skip totals','historical failure device name and reason','mobile page contained'],scope:'all APIs mocked; no business writes'};
 }finally{await page.goto('about:blank');await page.unroute(api);await page.setViewportSize({width:1440,height:1000});}
}
