async page=>{
 const base='http://127.0.0.1:3000',writes=[],previewRequests=[],deviceQueries=[];
 const platforms=[{plat_id:'jiaxing',platform_name:'嘉兴移动消防K8S平台',state:'1',connection_status:'online'},{plat_id:'other',platform_name:'其他平台',state:'1',connection_status:'online'}];
 await page.addInitScript(()=>{if(location.protocol==='http:')localStorage.setItem('token','isolated-device-retry');});
 await page.route(base+'/api/**',async route=>{
  const req=route.request(),path=new URL(req.url()).pathname;
  if(req.method()!=='GET')writes.push({path,body:req.postDataJSON()});
  if(path==='/api/projects')return route.fulfill({json:{items:[],total:0}});
  if(path==='/api/images/collections')return route.fulfill({json:[]});
  let data=[];
  if(path.endsWith('/managed-platforms'))data=platforms;
  else if(path.endsWith('/device-scenes/config'))data={scenes:['工厂','河边','道路'],scene_groups:[{name:'生产作业',scenes:['工厂']},{name:'自然环境',scenes:['河边']},{name:'交通场景',scenes:['道路']}],classification_enabled:true};
  else if(path.endsWith('/devices')){deviceQueries.push(new URL(req.url()).searchParams);data={total:201,items:[
   {plat_id:'jiaxing',platform_name:'嘉兴移动消防K8S平台',device_id:'failed',device_name:'失败设备',scene_status:'failed',scene_error:'模型超时',scene_source:'auto',representative_status:'failed',representative_error:'代表图请求超时，将按设备名称分类'},
   {plat_id:'jiaxing',platform_name:'嘉兴移动消防K8S平台',device_id:'done',device_name:'成功设备',scene_status:'completed',scene_labels:['工厂'],scene_source:'auto',representative_status:'ready',representative_url:'/preview'}]};}
  else if(path.endsWith('/preview')){previewRequests.push({path,headers:await req.allHeaders()});return route.fulfill({contentType:'image/png',body:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jJ/8AAAAASUVORK5CYII=','base64')});}
  else if(path.endsWith('/tasks'))data=[{task_id:'representatives',kind:'device-sync',status:'completed',platform_results:[{plat_id:'jiaxing',platform_name:'嘉兴移动消防K8S平台',status:'completed',count:2,device_total:2,image_count:1,image_reused_count:0,image_missing_count:0,image_failed_count:1}]}];
  else if(path.endsWith('/devices/sync')||path.endsWith('/devices/classify'))data={task_id:'fixture'};
  else if(!path.endsWith('/tasks')&&!path.endsWith('/device-capture/schedules'))throw Error('Unmocked API '+path);
  return route.fulfill({json:{code:0,data}});
 });
 const confirm=async()=>{const popup=page.locator('.ant-popconfirm:visible');await popup.getByRole('button',{name:/^确\s*定$/}).click();await popup.waitFor({state:'hidden'});};
 try{
  await page.goto(base+'/smarteye/index.html?project_id=fixture');
  await page.getByText('代表图请求超时，将按设备名称分类',{exact:true}).waitFor();
  await page.getByAltText('成功设备代表图',{exact:true}).waitFor();
  const enlarge=page.getByRole('button',{name:'放大查看成功设备代表图',exact:true});
  if(!await enlarge.count())throw Error('Representative thumbnail has no accessible enlarge action');
  await enlarge.click();
  const preview=page.getByRole('dialog',{name:'图片预览',exact:true});await preview.waitFor();
  await page.waitForFunction(()=>{const image=document.querySelector('[role="dialog"] img[alt="原图预览"]');return image&&image.complete&&image.naturalWidth>0;});
  if(previewRequests.length<2||previewRequests.some(request=>request.headers.authorization!=='Bearer isolated-device-retry'))throw Error('Thumbnail or enlarged image bypassed authentication');
  if(writes.length)throw Error('Opening preview started a business operation');
  await page.keyboard.press('Escape');await preview.waitFor({state:'hidden'});
  await enlarge.focus();await page.keyboard.press('Enter');await preview.waitFor();
  await page.keyboard.press('Escape');await preview.waitFor({state:'hidden'});
  await page.getByText('已选 0 台设备',{exact:true}).waitFor();
  await page.getByText('代表图 1 台（复用 0），无图 0，取图失败 1',{exact:false}).waitFor();
  const scene=page.getByRole('combobox',{name:'设备场景',exact:true});
  const chooseScene=async title=>{await scene.click();await page.locator('.ant-select-dropdown:visible .ant-select-tree-title').getByText(title,{exact:true}).click();await page.keyboard.press('Escape');await page.waitForTimeout(150);};
  await page.getByTitle('3',{exact:true}).click();
  await chooseScene('工厂');await chooseScene('河边');
  if(JSON.stringify(deviceQueries.at(-1).getAll('scene').sort())!==JSON.stringify(['工厂','河边'].sort())||deviceQueries.at(-1).get('page')!=='1')throw Error('Device scenes are not multi-select or page did not reset');
  const clearScenes=async()=>{await page.locator('.ant-select').filter({has:scene}).hover();await page.locator('.ant-select').filter({has:scene}).locator('.ant-select-clear').click();await page.waitForTimeout(150);};
  await clearScenes();if(deviceQueries.at(-1).getAll('scene').length)throw Error('Scene clear retained filters');
  await chooseScene('交通场景');await chooseScene('河边');
  if(JSON.stringify(deviceQueries.at(-1).getAll('scene').sort())!==JSON.stringify(['@group:交通场景','河边'].sort()))throw Error('Group and leaf scene selection lost');
  await clearScenes();
  await page.getByRole('checkbox',{name:'仅在线平台',exact:true}).check();
  await page.getByRole('combobox',{name:'设备平台',exact:true}).click();
  await page.locator('.ant-select-dropdown:visible').getByText('嘉兴移动消防K8S平台（在线）',{exact:true}).click();
  await page.getByRole('button',{name:'同步当前平台设备',exact:true}).click();
  if(JSON.stringify(writes.at(-1).body)!==JSON.stringify({platform_ids:['jiaxing']}))throw Error('Selected platform sync expanded scope');
  await page.getByRole('button',{name:'重跑失败分类',exact:true}).click();
  await page.getByText('重跑失败分类：嘉兴移动消防K8S平台？',{exact:true}).waitFor();
  await confirm();
  if(JSON.stringify(writes.at(-1).body)!==JSON.stringify({platform_ids:['jiaxing'],only_failed:true}))throw Error('Retry must cover all pages of selected platform');
  if(await page.getByRole('button',{name:'重跑分类',exact:true}).count()!==1)throw Error('Successful device offered failed retry');
  await page.getByRole('button',{name:'重跑分类',exact:true}).click();
  if(JSON.stringify(writes.at(-1).body)!==JSON.stringify({devices:[{plat_id:'jiaxing',device_id:'failed'}],only_failed:true}))throw Error('Single device retry wrong');
  await page.getByRole('combobox',{name:'设备平台',exact:true}).locator('..').hover();
  await page.locator('.ant-select').filter({has:page.getByRole('combobox',{name:'设备平台',exact:true})}).locator('.ant-select-clear').click();
  await page.getByRole('button',{name:'重跑失败分类',exact:true}).click();
  await page.getByText('重跑失败分类：全部在线平台？',{exact:true}).waitFor();await confirm();
  if(JSON.stringify(writes.at(-1).body)!==JSON.stringify({platform_ids:['jiaxing','other'],only_failed:true}))throw Error('Online-only retry expanded to all platforms');
  await page.getByRole('button',{name:'全部设备分类',exact:true}).click();
  await page.getByText('重新分类：全部在线平台？',{exact:true}).waitFor();await confirm();
  if(JSON.stringify(writes.at(-1).body)!==JSON.stringify({platform_ids:['jiaxing','other']}))throw Error('Full classification ignored online-only scope');
  await page.setViewportSize({width:390,height:844});await page.waitForTimeout(300);
  await page.screenshot({path:'output/playwright/device-failed-retry-mobile.png',fullPage:true});
  return {checks:['representative click and keyboard enlarge with authenticated image','Escape closes preview without selecting device or starting writes','scene multi-select preserves leaves and groups with repeated query keys','scene selection resets page and clear removes filters','representative preview and name fallback reason visible','platform representative counts visible','selected platform sync only','failed retry across all pages scoped by platform','single failed device retry','successful rows unchanged','online-only scope used for failed and full classification','mobile controls wrap'],scope:'all requests mocked; no business operations'};
 }catch(error){await page.screenshot({path:'output/playwright/device-failed-retry-error.png',fullPage:true});throw error;}
 finally{await page.goto('about:blank');await page.unroute(base+'/api/**');}
}
