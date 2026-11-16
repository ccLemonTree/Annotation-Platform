async page=>{
  page.setDefaultTimeout(12000);
  const base='http://localhost:3000',api=base+'/api/**',reads=[],writes=[];
  const detection={id:'11111111-1111-4111-8111-111111111111',project_name:'检测项目',task_type:'detection',status:'active',created_at:'2026-11-04T00:00:00Z',label_schema:{labels:['人']},total_assets:1};
  const caption={...detection,id:'22222222-2222-4222-8222-222222222222',project_name:'车辆属性',task_type:'caption',label_schema:{labels:['car']}};
  const images=[0,1,2].map(i=>({id:`image-${i}`,original_name:`existing-${i}.jpg`,scene_labels:['工厂'],scene_status:'completed',collection_names:['历史图片集'],has_imported_annotation:true,file_size:1000}));
  let fail=false;
  await page.addInitScript(()=>{if(location.protocol==='http:')localStorage.setItem('token','isolated-project-collection');});
  await page.route(api,async route=>{
    const req=route.request(),url=new URL(req.url()),path=url.pathname;
    if(req.method()==='GET')reads.push(url.href);else writes.push({path,body:req.postDataJSON()});
    let data;
    if(path==='/api/auth/me')data={id:1,username:'fixture',role:'admin',tenant_id:1,is_active:true};
    else if(path==='/api/projects')data={items:[detection,caption],total:2};
    else if(path==='/api/images/collections')data=[{id:'history',name:'历史图片集',image_count:3,ingest_config:{scenes:['工厂']}}];
    else if(path==='/api/images/ingest-settings')data={classification_enabled:false,scenes:['工厂']};
    else if(path==='/api/assets')data={items:url.searchParams.get('project_id')===detection.id?[{id:'asset-used',image_id:'image-0'}]:[],total:url.searchParams.get('project_id')===detection.id?1:0};
    else if(path==='/api/images')data={items:images,total:3};
    else if(path==='/api/images/selection')data={image_ids:['image-1','image-2'],total:2};
    else if(path==='/api/images/associate'){
      if(fail)return route.fulfill({status:503,json:{detail:'隔离关联失败'}});
      data={added_count:2,skipped_count:0,asset_ids:['a','b']};
    }else if(path.endsWith('/content'))return route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/>'});
    else throw Error('Unexpected API '+path);
    return route.fulfill({json:data});
  });
  const row=name=>page.getByRole('row').filter({hasText:name});
  try{
    await page.setViewportSize({width:1440,height:1100});await page.goto(base+'/projects');
    await page.getByRole('link',{name:detection.project_name,exact:true}).waitFor();
    const entry=row(detection.project_name).getByRole('button',{name:'从已有图片集添加',exact:true});
    if(!await entry.count())throw Error('Project list has no existing collection entry');
    await entry.click();
    const picker=page.getByRole('dialog',{name:`复用已有数据到 ${detection.project_name}`});await picker.waitFor();
    await picker.getByRole('combobox',{name:'选择器图片集',exact:true}).click();
    await page.locator('.ant-select-dropdown:visible').getByText('历史图片集（3）',{exact:true}).click();
    await page.waitForFunction(()=>!document.querySelector('.ant-drawer .ant-spin-spinning'));
    if(!await picker.getByRole('row').filter({hasText:'existing-0.jpg'}).getByRole('checkbox').isDisabled())throw Error('Existing project image selectable for duplicate attachment');
    await picker.getByRole('button',{name:'选择当前筛选全部图片',exact:true}).click();
    await picker.getByText('已选 2 张',{exact:true}).waitFor();
    await picker.locator('button').filter({hasText:`添加到 ${detection.project_name}`}).click();await picker.waitFor({state:'hidden'});
    const query=new URL(reads.find(value=>new URL(value).pathname==='/api/images/selection')).searchParams;
    if(query.get('collection_id')!=='history'||query.get('exclude_project_id')!==detection.id)throw Error('Collection or target project selection scope missing');
    const first=writes[0];if(first?.path!=='/api/images/associate'||first.body.project_id!==detection.id||JSON.stringify(first.body.image_ids)!==JSON.stringify(['image-1','image-2'])||first.body.include_source_annotations||first.body.include_imported_annotations)throw Error('Association target or default annotation behavior incorrect');
    if(page.url()!==base+'/projects')throw Error('Left project management during reuse');
    await row(caption.project_name).getByRole('button',{name:'从已有图片集添加',exact:true}).click();
    const other=page.getByRole('dialog',{name:`复用已有数据到 ${caption.project_name}`});await other.waitFor();
    if(!await other.getByRole('checkbox',{name:'带入已有YOLO标注',exact:true}).isDisabled())throw Error('Caption picker inherited detection task type');
    await other.getByRole('button',{name:'选择当前筛选全部图片',exact:true}).click();await other.getByText('已选 2 张',{exact:true}).waitFor();
    fail=true;await other.locator('button').filter({hasText:`添加到 ${caption.project_name}`}).click();
    await other.getByText('添加未全部完成；成功批次已生效，剩余选择已保留，请重试',{exact:true}).waitFor();
    await other.getByText('已选 2 张',{exact:true}).waitFor();
    fail=false;await other.locator('button').filter({hasText:`添加到 ${caption.project_name}`}).click();await other.waitFor({state:'hidden'});
    if(writes.length!==3||writes.slice(1).some(write=>write.path!=='/api/images/associate'||write.body.project_id!==caption.id))throw Error('Retry changed target or sent upload requests');
    await page.waitForFunction(()=>!document.querySelector('.ant-message-notice'));
    await page.locator('.ant-table-content').first().evaluate(el=>el.scrollLeft=0);
    await page.screenshot({path:'output/playwright/projects-existing-collection-desktop.png',fullPage:true,animations:'disabled'});
    await page.setViewportSize({width:390,height:844});await page.waitForTimeout(300);
    if(await page.locator('html').evaluate(el=>el.scrollWidth>el.clientWidth+1))throw Error('Project entry mobile overflow');
    await row(caption.project_name).getByRole('button',{name:'从已有图片集添加',exact:true}).click();await other.waitFor();
    await page.waitForTimeout(350);
    if(await page.locator('html').evaluate(el=>el.scrollWidth>el.clientWidth+1))throw Error('Collection picker mobile overflow');
    await page.screenshot({path:'output/playwright/projects-existing-collection-mobile.png',fullPage:true,animations:'disabled'});
    await other.getByRole('button',{name:'取消选图',exact:true}).click();
    return {checks:['direct project list collection reuse','collection filter and all filtered images','project duplicate exclusion','correct project identity and task type','no annotations copied by default','association failure keeps selection and retries','no uploads or route changes','390px containment'],scope:'root APIs mocked; no business database or storage writes'};
  }catch(error){await page.screenshot({path:'output/playwright/projects-existing-collection-failure.png',fullPage:true});throw error;}
  finally{await page.goto('about:blank');await page.unroute(api);}
}
