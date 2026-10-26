async(page)=>{
  const headers={Authorization:'Bearer '+await page.evaluate(()=>localStorage.getItem('token'))};
  const config=await(await page.request.get('http://localhost:3000/api/images/ingest-settings',{headers})).json();
  const groups=await(await page.request.get('http://localhost:3000/api/images/collections',{headers})).json();
  const target=groups[0];
  const original=await(await page.request.get('http://localhost:3000/api/images',{headers})).json();
  let item={...original.items[0],scene_labels:['工厂'],scene_status:'failed',scene_error:'分类超时'},requestBody;
  const pattern='http://localhost:3000/api/images?**',retry='http://localhost:3000/api/images/scene-classify';
  await page.route(pattern,async route=>{
    const query=new URL(route.request().url()).searchParams;
    const items=(!query.get('scene')||item.scene_labels.includes(query.get('scene')))&&(!query.get('scene_status')||item.scene_status===query.get('scene_status'))?[item]:[];
    await route.fulfill({json:{items,total:items.length,page:1,page_size:20}});
  });
  await page.route(retry,async route=>{
    requestBody=route.request().postDataJSON();item={...item,scene_status:'pending',scene_error:null};
    await route.fulfill({json:{queued_count:1}});
  });
  try{
    if(!config.classification_enabled)throw Error('Live configuration not enabled');
    await page.goto('http://localhost:3000/assets');
    const collection=page.getByRole('combobox',{name:'筛选图片集',exact:true});
    await collection.fill(target.name);await collection.press('ArrowDown');await collection.press('Enter');
    const scene=page.getByRole('combobox',{name:'筛选场景',exact:true});
    await scene.fill('工厂');await scene.press('ArrowDown');await scene.press('Enter');
    const status=page.getByRole('combobox',{name:'筛选场景分类状态',exact:true});
    await status.click();await page.locator('.ant-select-dropdown:visible').getByText('分类失败',{exact:true}).click();
    const row=page.getByRole('row').filter({hasText:item.original_name});
    await row.getByText('分类失败',{exact:true}).waitFor();
    await row.getByRole('checkbox').check();
    await page.getByRole('button',{name:'场景分类 / 重试（1）',exact:true}).click();
    await page.getByText('已排队分类 1 张图片',{exact:true}).waitFor();
    if(requestBody.image_ids[0]!==item.id||requestBody.image_ids.length!==1||requestBody.collection_id!==target.id)throw Error('Retry selected wrong image');
    await page.setViewportSize({width:390,height:844});
    if(await page.locator('html').evaluate(el=>el.scrollWidth>el.clientWidth+1))throw Error('Scene controls overflow');
    await page.screenshot({path:'output/playwright/scene-library-mobile.png'});
    return {checks:['scene and failed-state filters keep selected image','classification retry submits exact selected image','scene controls fit narrow layout'],scope:'classification write and displayed image state mocked; real catalog/config read only'};
  }finally{await page.unroute(pattern);await page.unroute(retry);await page.setViewportSize({width:1440,height:1000});}
}
