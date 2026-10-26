async(page)=>{
  const checks=[],writes=[],selectionQueries=[];
  const group='11111111-1111-4111-8111-111111111111',projectId='22222222-2222-4222-8222-222222222222';
  const config={classification_enabled:true,base_url:'http://model.test/v1',model:'fixture',scenes:['工厂','电梯'],min_long_edge:720};
  const project={id:projectId,project_name:'批量选图测试',tenant_id:2,task_type:'detection',status:'active',total_assets:1,annotated_count:0,reviewed_count:0};
  const images=Array.from({length:2005},(_,i)=>({id:'00000000-0000-4000-8000-'+String(i).padStart(12,'0'),
    original_name:`bulk-${String(i).padStart(4,'0')}.jpg`,blob_path:'fixture.jpg',content_hash:String(i),file_size:100,
    width:1280,height:720,format:'JPEG',source_type:'upload',batch_id:'fixture-batch',created_at:'2026-10-15T00:00:00Z',
    collection_names:['批量图片集'],scene_labels:[i%2?'电梯':'工厂'],scene_status:'completed',scene_error:null}));
  const used=new Set([images[0].id]);let failAt=0,associationCalls=0,selectionFailed=false;
  const matched=params=>images.filter(image=>(!params.get('scene')||image.scene_labels.includes(params.get('scene')))
    &&(!params.get('q')||image.original_name.includes(params.get('q'))));
  const paginate=(items,params)=>{const p=Number(params.get('page')||1),size=Number(params.get('page_size')||20);return {items:items.slice((p-1)*size,p*size),total:items.length,page:p,page_size:size};};
  const pattern='**/api/**';
  await page.route(pattern,async route=>{
    const request=route.request(),url=new URL(request.url()),path=url.pathname,params=url.searchParams;
    if(path==='/api/projects')return route.fulfill({json:paginate([project],params)});
    if(path==='/api/projects/'+projectId)return route.fulfill({json:project});
    if(path==='/api/images/collections')return route.fulfill({json:[{id:group,name:'批量图片集',image_count:images.length,ingest_config:config}]});
    if(path==='/api/images')return route.fulfill({json:paginate(matched(params),params)});
    if(path==='/api/images/selection'){
      if(selectionFailed)return route.fulfill({status:500,json:{detail:'fixture selection interrupted'}});
      selectionQueries.push(Object.fromEntries(params));
      const ids=matched(params).filter(image=>!params.get('exclude_project_id')||!used.has(image.id)).map(image=>image.id);
      return route.fulfill({json:{image_ids:ids,total:ids.length}});
    }
    if(path==='/api/assets')return route.fulfill({json:paginate(images.filter(image=>used.has(image.id)).map(image=>({...image,id:'asset-'+image.id,image_id:image.id,annotation_status:'pending'})),params)});
    if(path.endsWith('/content'))return route.fulfill({contentType:'image/png',body:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/l9sAAAAASUVORK5CYII=','base64')});
    if(request.method()!=='GET'){
      const data=request.postDataJSON();writes.push({path,...data});
      if(data.image_ids?.length>1000)throw Error('Write exceeds server limit');
      if(path==='/api/images/associate'){
        associationCalls++;
        if(associationCalls===failAt)return route.fulfill({status:500,json:{detail:'fixture batch interrupted'}});
        const count=data.image_ids.filter(id=>!used.has(id)).length;data.image_ids.forEach(id=>used.add(id));
        return route.fulfill({json:{added_count:count,asset_ids:data.image_ids}});
      }
      if(path==='/api/images/collections/assign')return route.fulfill({json:{collection_id:group,added_count:data.image_ids.length}});
      if(path==='/api/images/scene-classify')return route.fulfill({json:{queued_count:data.image_ids.length}});
      throw Error('Unexpected fixture write '+path);
    }
    return route.continue();
  });
  const choose=async(root,label,text)=>{
    const field=root.getByRole('combobox',{name:label,exact:true});await field.click();
    await page.locator('.ant-select-dropdown:visible .ant-select-item-option-content').getByText(text,{exact:true}).click();
  };
  const selected=async(root,n)=>{await root.getByText(`已选 ${n} 张`,{exact:true}).waitFor();};
  try{
    await page.setViewportSize({width:1700,height:950});await page.goto('http://localhost:3000/assets');
    await page.getByRole('row').filter({hasText:'bulk-0000.jpg'}).waitFor();
    await page.getByRole('checkbox',{name:'Select all',exact:true}).check();await selected(page,20);
    await page.locator('.ant-pagination-item-2').first().click();
    await page.getByRole('row').filter({hasText:'bulk-0020.jpg'}).waitFor();
    await page.getByRole('checkbox',{name:'Select all',exact:true}).check();await selected(page,40);
    checks.push('library preserves selection across pages beyond 20');
    await page.locator('.ant-pagination-options-size-changer').first().getByRole('combobox').click();
    await page.getByRole('option',{name:'100 条/页',exact:true}).click();
    await page.waitForFunction(()=>document.querySelectorAll('.ant-table-tbody tr.ant-table-row').length===100);
    await selected(page,40);checks.push('library page size 100 preserves selection');
    await choose(page,'筛选场景','工厂');await selected(page,0);
    await choose(page,'筛选图片集','批量图片集（2005）');
    await choose(page,'筛选场景分类状态','已分类');await choose(page,'筛选图片来源','上传');
    await page.getByRole('searchbox',{name:'筛选图片名称',exact:true}).fill('bulk-');await page.getByRole('searchbox',{name:'筛选图片名称',exact:true}).press('Enter');
    await page.getByRole('button',{name:'选择当前筛选全部图片',exact:true}).click();await selected(page,1003);
    const query=selectionQueries.at(-1);
    if(query.scene!=='工厂'||query.collection_id!==group||query.scene_status!=='completed'||query.source_type!=='upload'||query.q!=='bulk-')throw Error('Select-all lost filters');
    checks.push('library all-filtered selection includes scene/status/collection/source/name');
    await page.getByRole('button',{name:'加入图片集（1003）',exact:true}).click();
    let modal=page.getByRole('dialog',{name:'加入图片集',exact:true});await modal.getByLabel('归类图片集名称',{exact:true}).fill('批量图片集');
    await modal.getByRole('button',{name:'加 入',exact:true}).click();await modal.waitFor({state:'hidden'});
    if(writes.filter(write=>write.path.endsWith('/assign')).map(write=>write.image_ids.length).join()!=='1000,3')throw Error('Group batching incorrect');
    checks.push('library assigns more than 1000 images in bounded batches');
    await page.getByRole('button',{name:'选择当前筛选全部图片',exact:true}).click();await selected(page,1003);
    await page.getByRole('button',{name:/场景分类.*重试.*1003/}).click();await selected(page,0);
    if(writes.filter(write=>write.path.endsWith('/scene-classify')).map(write=>write.image_ids.length).join()!=='1000,3')throw Error('Classification batching incorrect');
    checks.push('library classification batches without a 1000-image selection ceiling');
    await page.goto('http://localhost:3000/projects/'+projectId+'/images');
    await page.getByRole('button',{name:'从图片库添加',exact:true}).click();
    let picker=page.getByRole('dialog',{name:'从图片库添加到 批量选图测试',exact:true});
    await picker.getByRole('row').filter({hasText:'bulk-0000.jpg'}).waitFor();
    if(!await picker.getByRole('row').filter({hasText:'bulk-0000.jpg'}).getByRole('checkbox').isDisabled())throw Error('Used image enabled');
    const bounds=await picker.locator('[aria-label="图片选择分页"]').boundingBox();
    if(!bounds||bounds.y+bounds.height>950)throw Error('Pagination below viewport');
    checks.push('project picker pagination stays visible and used image is disabled');
    await picker.getByRole('checkbox',{name:'Select all',exact:true}).check();await selected(picker,19);
    await picker.locator('.ant-pagination-item-2').click();await picker.getByRole('row').filter({hasText:'bulk-0020.jpg'}).waitFor();
    await picker.getByRole('checkbox',{name:'Select all',exact:true}).check();await selected(picker,39);
    checks.push('project picker retains cross-page selection beyond 20');
    await choose(picker,'选择器场景','工厂');await selected(picker,0);
    await picker.getByRole('button',{name:'选择当前筛选全部图片',exact:true}).click();await selected(picker,1002);
    if(selectionQueries.at(-1).exclude_project_id!==projectId||selectionQueries.at(-1).scene!=='工厂')throw Error('Project select-all failed to exclude used/filter');
    checks.push('project select-all uses scene filter and excludes already used images');
    await picker.locator('.ant-select').filter({has:page.getByRole('combobox',{name:'选择器场景',exact:true})}).locator('.ant-select-clear').click();
    await picker.getByRole('button',{name:'选择当前筛选全部图片',exact:true}).click();await selected(picker,2004);
    failAt=associationCalls+2;
    await picker.getByRole('button',{name:/添加到\s*批量选图测试/}).click();
    await picker.getByText(/成功批次已生效，剩余选择已保留/).waitFor();await selected(picker,1004);
    checks.push('failed second batch retains only uncompleted images');
    failAt=0;await picker.getByRole('button',{name:/添加到\s*批量选图测试/}).click();await picker.waitFor({state:'hidden'});
    const added=writes.filter(write=>write.path.endsWith('/associate'));
    if(added.map(write=>write.image_ids.length).join()!=='1000,1000,1000,4'||used.size!==2005)throw Error('Retry submitted completed images or missed remaining');
    checks.push('project retry submits remaining batches and preserves project location');
    await page.getByRole('button',{name:'从图片库添加',exact:true}).click();picker=page.getByRole('dialog',{name:'从图片库添加到 批量选图测试',exact:true});
    await picker.getByText('已在当前项目',{exact:true}).first().waitFor();
    await page.setViewportSize({width:390,height:844});
    const paging=await picker.locator('[aria-label="图片选择分页"]').boundingBox();
    const footer=await picker.getByRole('button',{name:/添加到\s*批量选图测试/}).boundingBox();
    if(!paging||paging.y+paging.height>844||!footer||footer.y+footer.height>844||await page.locator('html').evaluate(el=>el.scrollWidth>el.clientWidth+1))throw Error('Mobile paging/footer overflow');
    await page.screenshot({path:'output/playwright/image-bulk-selection-mobile.png'});
    checks.push('mobile picker pagination and footer remain within viewport');
    await page.setViewportSize({width:1700,height:950});await page.screenshot({path:'output/playwright/image-bulk-selection.png'});
    used.clear();used.add(images[0].id);
    await page.goto('http://localhost:3000/assets');
    await page.getByRole('button',{name:'选择当前筛选全部图片',exact:true}).click();await selected(page,2005);
    selectionFailed=true;await page.getByRole('button',{name:'选择当前筛选全部图片',exact:true}).click();
    await page.getByText('读取全部图片失败，原选择已保留，请重试',{exact:true}).waitFor();await selected(page,2005);
    selectionFailed=false;checks.push('selection read failure preserves all previous selections');
    await page.screenshot({path:'output/playwright/image-library-all-selection.png'});
    const previousWrites=writes.length;
    await page.getByRole('button',{name:'添加到标注项目（2005）',exact:true}).click();
    modal=page.getByRole('dialog',{name:'添加到标注项目',exact:true});
    await choose(modal,'图片添加到的项目','批量选图测试');
    await modal.getByRole('button',{name:'添加图片',exact:true}).click();await modal.waitFor({state:'hidden'});await selected(page,0);
    if(writes.slice(previousWrites).map(write=>write.image_ids.length).join()!=='1000,1000,5'||used.size!==2005)throw Error('Library project association batching incorrect');
    if(!page.url().endsWith('/assets'))throw Error('Library association changed page');
    checks.push('library adds all 2005 images in batches and remains on library page');
    return {checks,writesIntercepted:writes.length,fixtureImages:images.length,projectImagesNotModified:true};
  }finally{
    await page.unroute(pattern);await page.setViewportSize({width:1700,height:950});await page.goto('http://localhost:3000/assets');
  }
}
