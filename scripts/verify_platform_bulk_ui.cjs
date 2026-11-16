async page=>{
 const base='http://127.0.0.1:3000',api=base+'/api/**',writes=[];
 let task=null;
 const platforms=[{plat_id:'sx',platform_name:'绍兴测试平台',base_url:'http://fixture.test',state:'1',connection_status:'unchecked'},
  {plat_id:'disabled',platform_name:'停用测试平台',base_url:'http://disabled.test',state:'0',connection_status:'unchecked'},
  {plat_id:'unset',platform_name:'未配置测试平台',base_url:'',state:'1',connection_status:'unchecked'}];
 await page.addInitScript(()=>{if(location.protocol==='http:')localStorage.setItem('token','isolated-platform-bulk');});
 await page.route(api,route=>{
  const req=route.request(),path=new URL(req.url()).pathname;
  if(req.method()!=='GET')writes.push({path,body:req.postData()?req.postDataJSON():null});
  if(path==='/api/projects')return route.fulfill({json:{items:[],total:0}});
  if(path==='/api/images/collections')return route.fulfill({json:[]});
  let data=[];
  if(path.endsWith('/managed-platforms'))data=platforms;
  else if(path.endsWith('/managed-platforms/check-all')){
   if(req.method()==='POST')task={task_id:'batch-check',kind:'platform-check',status:'running',total_count:3,processed_count:0,online_count:0,error_count:0,unconfigured_count:0,progress:0};
   data=task;
  }else if(path.endsWith('/tasks/batch-check/cancel')){task={...task,status:'cancelled'};data={};}
  else if(path.endsWith('/devices'))data={items:[],total:0};
  else if(path.endsWith('/device-scenes/config'))data={scenes:['工厂'],classification_enabled:false};
  else if(!path.endsWith('/tasks')&&!path.endsWith('/device-capture/schedules'))throw Error('Unmocked API '+path);
  return route.fulfill({json:{code:0,data}});
 });
 try{
  await page.setViewportSize({width:1440,height:1100});
  await page.goto(base+'/smarteye/index.html?project_id=fixture');
  await page.getByRole('menuitem').filter({hasText:'平台管理'}).click();
  const search=page.getByLabel('搜索平台',{exact:true});await search.focus();
  const style=await search.evaluate(el=>{const input=getComputedStyle(el),wrapper=el.closest('.ant-input-affix-wrapper');return {outline:input.outlineWidth,outlineStyle:input.outlineStyle,border:input.borderTopWidth,height:wrapper.getBoundingClientRect().height,padding:input.paddingTop,className:el.className,focusVisible:el.matches(':focus-visible')};});
  const refresh=await page.locator('button').filter({hasText:'刷新平台'}).boundingBox();
  if((style.outlineStyle!=='none'&&style.outline!=='0px')||style.border!=='0px'||Math.abs(style.height-refresh.height)>1)throw Error('Nested input style pollution '+JSON.stringify({input:style,buttonHeight:refresh.height}));
  await search.fill('绍兴');
  await page.getByRole('combobox',{name:'平台在线状态',exact:true}).click();
  await page.locator('.ant-select-dropdown:visible .ant-select-item-option-content').getByText('在线平台',{exact:true}).click();
  await page.locator('.ant-empty-description').getByText('暂无数据',{exact:true}).waitFor();
  await page.getByRole('button',{name:'一键检测所有平台',exact:true}).click();
  await page.getByRole('button',{name:'停止检测',exact:true}).waitFor();
  if(writes[0].path!=='/api/smarteye/managed-platforms/check-all'||writes[0].body?.platform_ids)throw Error('Batch was limited by current search');
  await page.reload();await page.getByRole('menuitem').filter({hasText:'平台管理'}).click();
  await page.getByRole('button',{name:'停止检测',exact:true}).waitFor();
  task={...task,status:'completed',processed_count:3,online_count:1,error_count:1,unconfigured_count:1,progress:100};
  platforms[0].connection_status='online';platforms[1].connection_status='error';platforms[2].connection_status='unconfigured';
  await page.getByText('在线 1 个，连接失败 1 个，未配置 1 个',{exact:false}).waitFor();
  await page.getByRole('button',{name:'一键检测所有平台',exact:true}).click();
  await page.getByRole('button',{name:'停止检测',exact:true}).click();
  await page.getByText('检测已停止',{exact:false}).waitFor();
  await page.waitForTimeout(350);
  if(!writes.some(w=>w.path.endsWith('/tasks/batch-check/cancel')))throw Error('Stop did not cancel task');
  await search.focus();await page.screenshot({path:'output/playwright/platform-bulk-check.png',fullPage:true});
  await page.setViewportSize({width:390,height:844});await page.waitForTimeout(300);
  if(await page.locator('html').evaluate(el=>el.scrollWidth>el.clientWidth+1))throw Error('Mobile overflow');
  return {checks:['one search border and aligned height','batch covers all platforms despite search and connection filter','progress resumes after page reload','completed counts and platform states refresh','stop control cancels batch','390px layout'],scope:'all APIs mocked; no real platform logins'};
 }catch(error){await page.screenshot({path:'output/playwright/platform-bulk-failure.png',fullPage:true});throw error;}
 finally{await page.goto('about:blank');await page.unroute(api);await page.setViewportSize({width:1440,height:1000});}
}
