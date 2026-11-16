async page=>{
 const base='http://127.0.0.1:3000',api=base+'/api/**',queries=[];
 const devices=[{device_id:'empty1',device_name:'小学设备',scene_status:'completed',scene_labels:[],scene_source:'auto'},
  {device_id:'hit',device_name:'已匹配设备',scene_status:'completed',scene_labels:['学校'],scene_source:'auto'},
  {device_id:'empty2',device_name:'路口设备',scene_status:'unmatched',scene_labels:[],scene_source:'auto'},
  {device_id:'failed',device_name:'失败设备',scene_status:'failed',scene_labels:[],scene_error:'模型超时'}].map(d=>({...d,plat_id:'a',platform_name:'测试平台'}));
 await page.addInitScript(()=>{if(location.protocol==='http:')localStorage.setItem('token','isolated-unmatched');});
 await page.route(api,route=>{
  const req=route.request(),url=new URL(req.url()),path=url.pathname;queries.push({path,status:url.searchParams.get('scene_status')});
  if(req.method()!=='GET')throw Error('Unexpected write '+path);
  if(path==='/api/auth/me')return route.fulfill({json:{id:1,role:'admin',username:'fixture',tenant_id:1}});
  if(path==='/api/projects')return route.fulfill({json:{items:[],total:0}});
  if(path==='/api/images/collections')return route.fulfill({json:[]});
  if(path==='/api/images/ingest-settings')return route.fulfill({json:{scenes:['学校'],min_long_edge:0,classification_enabled:true}});
  if(path==='/api/images')return route.fulfill({json:{items:[{id:'empty-image',original_name:'empty.jpg',scene_status:'completed',scene_labels:[],source_type:'smarteye',file_size:1}],total:1}});
  if(path.endsWith('/content'))return route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg"/>'});
  let data=[];
  if(path.endsWith('/managed-platforms'))data=[{plat_id:'a',platform_name:'测试平台',state:'1',connection_status:'online',device_count:4,classified_count:1,scene_failed_count:1,scene_unmatched_count:2,scene_pending_count:0}];
  else if(path.endsWith('/device-scenes/config'))data={scenes:['学校'],classification_enabled:true};
  else if(path.endsWith('/devices')){
   const status=url.searchParams.get('scene_status');
   const items=devices.filter(d=>!status||(status==='unmatched'?(d.scene_status==='unmatched'||d.scene_status==='completed'&&!d.scene_labels.length):status==='completed'?d.scene_status==='completed'&&d.scene_labels.length:d.scene_status===status));
   data={items,total:items.length};
  }else if(path.endsWith('/tasks'))data=[{task_id:'new',name:'新记录',kind:'device-scenes',status:'completed',processed_count:4,failed_count:1,unmatched_count:2},{task_id:'old',name:'历史记录',kind:'device-scenes',status:'completed',processed_count:3,failed_count:0}];
  else if(!path.endsWith('/device-capture/schedules'))throw Error('Unmocked API '+path);
  return route.fulfill({json:{code:0,data}});
 });
 try{
  await page.setViewportSize({width:1440,height:1100});
  await page.goto(base+'/smarteye/index.html?project_id=fixture');
  const empty=page.getByRole('row').filter({hasText:'小学设备'});
  await empty.getByText('未匹配场景',{exact:true}).waitFor();
  if(await empty.getByText('已分类',{exact:true}).count())throw Error('Empty result still shown as classified');
  await empty.getByText('暂无匹配场景',{exact:true}).waitFor();
  const old=page.getByRole('row').filter({has:page.getByText('历史记录',{exact:true})});
  await old.getByText(/旧记录未区分是否匹配场景/).waitFor();
  if((await old.textContent()).includes('成功 3 台'))throw Error('Legacy request success misrepresented as scene match');
  await page.getByRole('row').filter({has:page.getByText('新记录',{exact:true})}).getByText(/成功 1 台，未匹配 2 台/).waitFor();
  const platform=page.getByRole('row').filter({has:page.getByRole('button',{name:'查看失败设备',exact:true})});
  await platform.getByRole('button',{name:'2',exact:true}).click();
  await page.waitForTimeout(300);
  if(!queries.some(q=>q.path.endsWith('/devices')&&q.status==='unmatched'))throw Error('Unmatched drilldown missing');
  if(await page.getByRole('cell',{name:/已匹配设备/}).count())throw Error('Matched device leaked into unmatched filter');
  await page.screenshot({path:'output/playwright/scene-unmatched-device.png',fullPage:true});
  await page.goto(base+'/assets');
  await page.locator('tbody .ant-tag').getByText('未匹配场景',{exact:true}).waitFor();
  await page.getByRole('combobox',{name:'筛选场景分类状态',exact:true}).click();
  await page.locator('.ant-select-dropdown:visible').getByText('未匹配场景',{exact:true}).click();
  await page.waitForTimeout(300);
  if(!queries.some(q=>q.path==='/api/images'&&q.status==='unmatched'))throw Error('Image unmatched filter missing');
  return {checks:['empty labels show unmatched rather than classified','scene cell explains empty result','platform unmatched count drills down','new task splits matched/unmatched/error','legacy task does not invent match counts','image status and filter are consistent'],scope:'all APIs mocked; no business writes'};
 }finally{await page.goto('about:blank');await page.unroute(api);}
}
