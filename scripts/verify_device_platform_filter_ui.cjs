async page=>{
 const base='http://127.0.0.1:3000',writes=[],deviceQueries=[];
 let platforms=[{plat_id:'online',platform_name:'在线测试',state:'1',connection_status:'online'},{plat_id:'offline',platform_name:'离线测试',state:'1',connection_status:'error'},{plat_id:'unknown',platform_name:'未检测测试',state:'1',connection_status:'unchecked'},{plat_id:'disabled',platform_name:'停用测试',state:'0',connection_status:'online'}];
 let config={classification_enabled:true,scenes:['工厂'],base_url:'http://fixture/v1',model:'fixture',min_long_edge:960,scene_prompt:'preserve image prompt'};
 await page.addInitScript(()=>localStorage.setItem('token','isolated-device-filter'));
 const api=base+'/api/**';
 await page.route(api,async route=>{
  const req=route.request(),path=new URL(req.url()).pathname;
  if(req.method()!=='GET')writes.push({path,body:req.postDataJSON(),url:req.url()});
  if(path==='/api/projects')return route.fulfill({json:{items:[],total:0}});
  if(path==='/api/images/collections')return route.fulfill({json:[]});
  if(path==='/api/model-services/prompt-defaults')return route.fulfill({json:{scene:'fixture'}});
  if(path==='/api/images/ingest-settings'){if(req.method()==='PUT')config=req.postDataJSON();return route.fulfill({json:config});}
  let data=[];
  if(path.endsWith('/managed-platforms'))data=platforms;
  else if(path.endsWith('/device-scenes/config'))data={scenes:config.scenes,classification_enabled:config.classification_enabled};
  else if(path.endsWith('/devices')){deviceQueries.push(new URL(req.url()).searchParams.get('has_image'));data={items:[],total:0};}
  else if(path.endsWith('/devices/sync'))data={task_id:'isolated-sync'};
  else if(!path.endsWith('/tasks')&&!path.endsWith('/device-capture/schedules'))throw Error('Unmocked API '+path);
  return route.fulfill({json:{code:0,data}});
 });
 const choose=async(text)=>{await page.getByRole('combobox',{name:'设备平台',exact:true}).click();await page.locator('.ant-select-dropdown:visible .ant-select-item-option-content').getByText(text,{exact:true}).click();};
 try{
  await page.goto(base+'/smarteye/index.html?project_id=fixture');
  const imageFilter=page.getByRole('combobox',{name:'设备图片筛选',exact:true});
  for(const [label,value] of [['有图片','yes'],['无图片','no'],['全部图片状态','']]){
   await imageFilter.click();
   await page.locator('.ant-select-dropdown:visible .ant-select-item-option-content').getByText(label,{exact:true}).click();
   await page.waitForTimeout(300);
   if(deviceQueries.at(-1)!==value)throw Error('Wrong image filter query: '+deviceQueries.at(-1));
  }
  await choose('离线测试（未连接）');
  await page.getByRole('checkbox',{name:'仅在线平台',exact:true}).check();
  await page.waitForTimeout(300);
  await page.getByRole('combobox',{name:'设备平台',exact:true}).click();
  await page.locator('.ant-select-dropdown:visible .ant-select-item-option-content').first().waitFor();
  await page.waitForTimeout(300);
  const options=await page.locator('.ant-select-dropdown:visible .ant-select-item-option-content').allTextContents();
  if(JSON.stringify(options)!==JSON.stringify(['在线测试（在线）']))throw Error('Online filter leaked inactive platforms: '+JSON.stringify(options));
  await page.keyboard.press('Escape');
  await page.getByRole('button',{name:'同步在线平台设备',exact:true}).click();
  if(JSON.stringify(writes.at(-1).body.platform_ids)!==JSON.stringify(['online']))throw Error('Sync did not respect online filter/clear previous offline choice');
  await page.getByRole('button',{name:'场景分类设置',exact:true}).click();
  const modal=page.getByRole('dialog',{name:'设备场景分类设置',exact:true});
  const tags=modal.getByRole('combobox',{name:'具体场景 1',exact:true});await tags.fill('医院');await tags.press('Enter');
  await modal.getByRole('button',{name:'保存分类设置',exact:true}).click();await modal.waitFor({state:'hidden'});
  const saved=writes.find(w=>w.path==='/api/images/ingest-settings');
  if(!saved||!saved.body.scenes.includes('医院')||saved.body.min_long_edge!==960||saved.body.scene_prompt!=='preserve image prompt'||saved.url.includes('collection_id'))throw Error('Wrong global config or unrelated settings overwritten');
  platforms=platforms.map(p=>({...p,connection_status:'error'}));
  await page.reload();await page.getByRole('checkbox',{name:'仅在线平台',exact:true}).check();
  if(!await page.getByRole('button',{name:'同步在线平台设备',exact:true}).isDisabled())throw Error('No online platforms would expand sync to all');
  return {checks:['image presence filters yes/no/all reach API','online-only options exclude offline/unchecked/disabled','previous offline choice cleared','sync targets online IDs','empty online set disables sync','scene editor uses global config and preserves unrelated fields'],scope:'all API calls mocked; no real sync/config changes'};
 }catch(error){await page.screenshot({path:'output/playwright/device-online-filter-failure.png',fullPage:true});throw error;}finally{await page.goto('about:blank');await page.unroute(api);}
}
