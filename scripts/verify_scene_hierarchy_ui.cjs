async page=>{
 const base='http://127.0.0.1:3000',queries=[],writes=[],errors=[];
 let config={classification_enabled:true,base_url:'http://fixture/v1',model:'fixture',scenes:['道路','路口','工厂'],scene_groups:[{name:'交通场景',scenes:['道路','路口']},{name:'生产作业',scenes:['工厂']}],min_long_edge:720,scene_prompt:'preserved prompt'};
 const project={id:'project-fixture',project_name:'测试项目',task_type:'detection',label_schema:{labels:['人']},status:'active',total_assets:1};
 const image={id:'image-fixture',original_name:'road.jpg',blob_path:'road.jpg',source_type:'smarteye',scene_labels:['道路'],scene_status:'completed',collection_names:['测试集'],file_size:1};
 const api=base+'/api/**',onError=error=>errors.push(error.message);
 page.on('pageerror',onError);
 await page.addInitScript(()=>{if(location.protocol==='http:')localStorage.setItem('token','isolated-scene-hierarchy');});
 await page.route(api,async route=>{
  const req=route.request(),url=new URL(req.url()),path=url.pathname;
  queries.push({path,params:Object.fromEntries(url.searchParams)});
  if(req.method()!=='GET')writes.push({path,body:req.postDataJSON(),collection:url.searchParams.get('collection_id')});
  if(path==='/api/auth/me')return route.fulfill({json:{id:1,role:'admin',username:'fixture',tenant_id:1}});
  if(path==='/api/projects')return route.fulfill({json:{items:[project],total:1}});
  if(path==='/api/projects/project-fixture')return route.fulfill({json:project});
  if(path==='/api/images/collections')return route.fulfill({json:[{id:'group-fixture',name:'测试集',image_count:1,ingest_config:config}]});
  if(path==='/api/assets/collections')return route.fulfill({json:[{id:'group-fixture',name:'测试集'}]});
  if(path==='/api/images/ingest-settings'){if(req.method()==='PUT')config=req.postDataJSON();return route.fulfill({json:config});}
  if(path==='/api/model-services')return route.fulfill({json:[]});
  if(path==='/api/model-services/prompt-defaults')return route.fulfill({json:{scene:'default prompt'}});
  if(path==='/api/images')return route.fulfill({json:{items:[image],total:1}});
  if(path==='/api/assets')return route.fulfill({json:{items:[{...image,id:'asset-fixture',project_id:project.id}],total:1},headers:{'X-Asset-Scene-Filter':'1'}});
  if(path.endsWith('/content'))return route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg"/>'});
  let data=[];
  if(path.endsWith('/managed-platforms'))data=[{plat_id:'a',platform_name:'测试平台',state:'1',connection_status:'online',device_count:1}];
  else if(path.endsWith('/device-scenes/config'))data=config;
  else if(path.endsWith('/devices'))data={items:[{plat_id:'a',device_id:'one',device_name:'道路摄像头',platform_name:'测试平台',scene_labels:['道路'],scene_status:'completed'}],total:1};
  else if(path.endsWith('/scene'))data={};
  else if(!path.endsWith('/tasks')&&!path.endsWith('/device-capture/schedules'))throw Error('Unmocked API '+path);
  return route.fulfill({json:{code:0,data}});
 });
 const chooseTree=async(label,title)=>{
  await page.getByRole('combobox',{name:label,exact:true}).click();
  await page.locator('.ant-select-dropdown:visible .ant-select-tree-title').getByText(title,{exact:true}).click();
  await page.waitForTimeout(300);
 };
 try{
  await page.setViewportSize({width:1440,height:1100});
  await page.goto(base+'/smarteye/index.html?project_id=project-fixture');
  await page.getByText('交通场景 / 道路',{exact:true}).waitFor();
  await chooseTree('设备场景','交通场景');
  if(!queries.some(q=>q.path.endsWith('/devices')&&q.params.scene==='@group:交通场景'))throw Error('Parent device filter missing');
  await chooseTree('设备场景','路口');
  if(!queries.some(q=>q.path.endsWith('/devices')&&q.params.scene==='路口'))throw Error('Leaf device filter missing');
  await page.getByRole('button',{name:'修正场景',exact:true}).click();
  await chooseTree('人工设备场景','工厂');await page.keyboard.press('Escape');
  await page.getByRole('button',{name:'保存场景',exact:true}).click();
  const manual=writes.find(w=>w.path.endsWith('/scene'));
  if(JSON.stringify(manual.body.scene_labels)!==JSON.stringify(['道路','工厂']))throw Error('Manual labels rewritten to group paths');
  await page.getByRole('button',{name:'场景分类设置',exact:true}).click();
  const modal=page.getByRole('dialog',{name:'设备场景分类设置',exact:true});
  await modal.getByRole('textbox',{name:'场景大类名称 1',exact:true}).fill('交通出行');
  const tags=modal.getByRole('combobox',{name:'具体场景 1',exact:true});await tags.fill('停车场');await tags.press('Enter');
  await modal.getByRole('button',{name:'添加场景大类',exact:true}).click();
  await modal.getByRole('textbox',{name:'场景大类名称 3',exact:true}).fill('自然环境');
  await modal.getByRole('button',{name:'删除场景大类 3',exact:true}).click();
  await modal.getByRole('button',{name:'添加场景大类',exact:true}).click();
  await modal.getByRole('textbox',{name:'场景大类名称 3',exact:true}).fill('自然环境');
  const nature=modal.getByRole('combobox',{name:'具体场景 3',exact:true});await nature.fill('河边');await nature.press('Enter');
  await page.keyboard.press('Escape');await page.waitForTimeout(300);
  if(await modal.locator('.ant-form-item-has-error').count())throw Error('Valid hierarchy still shows a validation error');
  await page.screenshot({path:'output/playwright/scene-hierarchy-settings.png',fullPage:true});
  await modal.getByRole('button',{name:'保存分类设置',exact:true}).click();await modal.waitFor({state:'hidden'});
  const saved=writes.find(w=>w.path==='/api/images/ingest-settings');
  if(saved.collection||saved.body.scene_groups[0].name!=='交通出行'||!saved.body.scenes.includes('停车场')||saved.body.scene_groups.length!==3||saved.body.min_long_edge!==720||saved.body.scene_prompt!=='preserved prompt')throw Error('Wrong hierarchy save or unrelated settings overwritten');
  await page.goto(base+'/assets');
  await page.getByText('交通出行 / 道路',{exact:true}).waitFor();
  await chooseTree('筛选场景','交通出行');
  if(!queries.some(q=>q.path==='/api/images'&&q.params.scene==='@group:交通出行'))throw Error('Image parent filter missing');
  await page.getByRole('button',{name:'图片集设置',exact:true}).click();
  const settings=page.getByRole('dialog',{name:'图片集入库与场景设置',exact:true});
  await settings.getByRole('combobox',{name:'设置目标图片集',exact:true}).click();
  await page.locator('.ant-select-dropdown:visible').getByText('测试集',{exact:true}).click();
  const children=settings.getByRole('combobox',{name:'具体场景 1',exact:true});await children.fill('桥梁');await children.press('Enter');
  await page.keyboard.press('Escape');
  await settings.getByRole('button',{name:'保存设置',exact:true}).click();await settings.waitFor({state:'hidden'});
  if(!writes.some(w=>w.collection==='group-fixture'&&w.body.scenes.includes('桥梁')))throw Error('Collection hierarchy save missing');
  await page.goto(base+'/projects/project-fixture/images');
  await chooseTree('项目图片场景','交通出行');
  if(!queries.some(q=>q.path==='/api/assets'&&q.params.scene==='@group:交通出行'))throw Error('Project parent filter missing');
  await page.setViewportSize({width:390,height:844});
  if(await page.locator('html').evaluate(el=>el.scrollWidth>el.clientWidth+1))throw Error('Mobile overflow');
  if(errors.length)throw Error(errors.join(';'));
  return {checks:['device parent and leaf filter','manual multi-label leaves stay compatible','create rename delete parent and add children','global settings preserve unrelated fields','library and project parent filters','collection scoped editor','390px layout'],scope:'all APIs mocked; no real sync/classification/config changes'};
 }catch(error){await page.screenshot({path:'output/playwright/scene-hierarchy-failure.png',fullPage:true});throw error;}
 finally{page.off('pageerror',onError);await page.goto('about:blank');await page.unroute(api);await page.setViewportSize({width:1440,height:1000});}
}
