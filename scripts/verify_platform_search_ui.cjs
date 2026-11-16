async page=>{
 const base='http://127.0.0.1:3000',api=base+'/api/**',requests=[];
 const rows=Array.from({length:26},(_,i)=>({plat_id:`fixture-${i}`,platform_name:`普通平台${i}`,base_url:`http://fixture${i}.test`,state:i===0?'0':'1',connection_status:['online','error','unchecked','unconfigured','unknown'][i%5],device_count:0}));
 rows[23]={...rows[23],plat_id:'shaoxing-k8s-id',platform_name:'绍兴移动k8s平台',base_url:'http://36.212.5.102:30869',connection_status:'online'};
 await page.addInitScript(()=>{if(location.protocol==='http:')localStorage.setItem('token','isolated-platform-search');});
 await page.route(api,route=>{
  const req=route.request(),path=new URL(req.url()).pathname;requests.push({path,method:req.method()});
  if(req.method()!=='GET')throw Error('Unexpected write');
  if(path==='/api/projects')return route.fulfill({json:{items:[],total:0}});
  if(path==='/api/images/collections')return route.fulfill({json:[]});
  let data=[];
  if(path.endsWith('/managed-platforms'))data=rows;
  else if(path.endsWith('/managed-platforms/check-all'))data=null;
  else if(path.endsWith('/devices'))data={items:[],total:0};
  else if(path.endsWith('/device-scenes/config'))data={scenes:['工厂'],classification_enabled:false};
  else if(path.endsWith('/platform-list'))data=[];
  return route.fulfill({json:{code:0,data}});
 });
 try{
  await page.goto(base+'/smarteye/index.html?project_id=fixture');
  await page.getByRole('menuitem').filter({hasText:'平台管理'}).click();
  const search=page.getByLabel('搜索平台',{exact:true});
  await search.waitFor();
  const status=page.getByRole('combobox',{name:'平台在线状态',exact:true});
  if(!await status.count())throw Error('Missing online/offline platform filter');
  const filter=async label=>{await status.click();await page.locator('.ant-select-dropdown:visible .ant-select-item-option-content').getByText(label,{exact:true}).click();};
  await page.getByTitle('3',{exact:true}).click();
  const before=requests.length;
  await search.fill('  K8S  ');
  const target=page.getByRole('cell',{name:'绍兴移动k8s平台',exact:true});await target.waitFor();
  await search.fill('绍兴');await target.waitFor();
  if(await page.getByRole('cell',{name:/普通平台/}).count())throw Error('Unmatched platforms remain visible');
  await search.fill('36.212.5');await target.waitFor();
  await search.fill('shaoxing-k8s');await target.waitFor();
  await search.fill('不存在的平台');await page.locator('.ant-empty-description').getByText('暂无数据',{exact:true}).waitFor();
  await search.fill('');await page.getByRole('cell',{name:'普通平台0',exact:true}).waitFor();
  await page.getByTitle('3',{exact:true}).click();
  await filter('在线平台');await page.getByRole('cell',{name:'普通平台0',exact:true}).waitFor();
  if(await page.locator('.ant-table-tbody > tr.ant-table-row').count()!==7)throw Error('Online filter did not include all pages/disabled-online platform');
  await page.getByText('共 7 个平台',{exact:true}).waitFor();
  await filter('不在线平台');await page.getByRole('cell',{name:'普通平台1',exact:true}).waitFor();
  await page.getByText('共 19 个平台',{exact:true}).waitFor();
  for(const i of [2,3,4])await page.getByRole('cell',{name:`普通平台${i}`,exact:true}).waitFor();
  if(await target.count())throw Error('Online platform leaked into offline filter');
  await search.fill('K8S');await page.locator('.ant-empty-description').getByText('暂无数据',{exact:true}).waitFor();
  await filter('在线平台');await target.waitFor();
  await search.fill('');await page.getByText('共 7 个平台',{exact:true}).waitFor();
  if(requests.length!==before)throw Error('Typing performed network requests');
  await search.fill('K8S');rows[23].connection_status='error';
  await page.locator('button').filter({hasText:'刷新平台'}).click();
  await page.locator('.ant-empty-description').getByText('暂无数据',{exact:true}).waitFor();
  await filter('不在线平台');await target.waitFor();
  await filter('全部平台');await search.fill('');await page.getByText('共 26 个平台',{exact:true}).waitFor();
  await page.setViewportSize({width:390,height:844});await page.waitForTimeout(300);
  if(await page.locator('html').evaluate(el=>el.scrollWidth>el.clientWidth+1))throw Error('Mobile overflow');
  return {checks:['case-insensitive partial name search','partial address and identifier search','trim spaces','reset page on search or connection filter','online/offline/all filtered before pagination','offline includes error/unconfigured/unchecked/unknown','connection status independent of enabled switch','combined search and status filter','refresh updates active filtered results','no network requests when filtering','390px layout'],scope:'API mocked; no real platform tests or changes'};
 }catch(error){await page.screenshot({path:'output/playwright/platform-search-failure.png',fullPage:true});throw error;}
 finally{await page.goto('about:blank');await page.unroute(api);await page.setViewportSize({width:1440,height:1000});}
}
