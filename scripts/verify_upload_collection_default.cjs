async(page)=>{
  const checks=[];
  const config={classification_enabled:false,base_url:'http://model.test/v1',model:'fixture',scenes:['工厂'],min_long_edge:720};
  let collections=[{id:'11111111-1111-4111-8111-111111111111',name:'已有甲',image_count:0,ingest_config:config},
    {id:'22222222-2222-4222-8222-222222222222',name:'已有乙',image_count:0,ingest_config:config}];
  await page.route('**/api/images/collections',route=>route.fulfill({json:collections}));
  await page.route('**/api/images?**',route=>route.fulfill({json:{items:[],total:0,page:1,page_size:20}}));
  await page.route('**/api/image-uploads**',async route=>{
    const request=route.request(),path=new URL(request.url()).pathname;
    if(path.endsWith('/config'))return route.fulfill({json:{chunk_size:8388608,max_file_size:10737418240,max_image_size:104857600,max_expanded_size:21474836480,max_entries:10000,retention_days:7}});
    if(request.method()==='GET')return route.fulfill({json:[]});
    const data=request.postDataJSON();
    return route.fulfill({json:{id:'33333333-3333-4333-8333-333333333333',collection_id:collections.find(group=>group.name===data.collection_name)?.id,
      status:'completed',result:{imported:1,duplicates:0,filtered:0,invalid:0,ignored:0,errors:[]}}});
  });
  const dialog=()=>page.getByRole('dialog',{name:'上传到图片库',exact:true});
  const field=()=>dialog().getByRole('combobox',{name:'上传图片集名称'});
  const open=()=>page.locator('.page-toolbar-actions').getByRole('button',{name:/上传图片/}).click();
  try{
    await page.goto('http://localhost:3000/assets');await page.evaluate(()=>localStorage.removeItem('image-upload-collection'));
    await page.reload();await page.getByRole('combobox',{name:'筛选图片集'}).click();await page.getByText('已有甲（0）',{exact:true}).waitFor();await page.keyboard.press('Escape');
    await open();if(await field().inputValue()!=='已有甲')throw Error('Upload default is not an existing collection');
    checks.push('default target is an existing collection instead of today date');
    await field().fill('已有乙');await dialog().getByLabel('上传图片或压缩包',{exact:true}).setInputFiles({name:'default.jpg',mimeType:'image/jpeg',buffer:Buffer.from('fixture')});
    await dialog().getByRole('button',{name:'开始 / 继续上传',exact:true}).click();await dialog().getByText(/新增 1，重复 0/).waitFor();
    await page.reload();await open();if(await field().inputValue()!=='已有乙')throw Error('Reload forgot the previous valid collection');
    checks.push('last used valid collection survives a page reload');
    await dialog().getByRole('button',{name:'关 闭',exact:true}).click();
    const filter=page.getByRole('combobox',{name:'筛选图片集'});await filter.fill('已有甲');await filter.press('ArrowDown');await filter.press('Enter');
    await open();if(await field().inputValue()!=='已有甲')throw Error('Selected collection does not take priority');
    checks.push('current selected collection takes priority over remembered target');
    await page.evaluate(()=>localStorage.setItem('image-upload-collection','已不存在的图片集'));await page.reload();
    await page.getByRole('combobox',{name:'筛选图片集'}).click();await page.getByText('已有甲（0）',{exact:true}).waitFor();await page.keyboard.press('Escape');
    await page.getByRole('button',{name:'上传图片',exact:true}).last().click();
    if(await field().inputValue()!=='已有甲')throw Error('Empty-state upload used stale/date default');
    checks.push('empty-state entry ignores removed collections and uses existing target');
    collections=[];await page.reload();await open();
    if(await field().inputValue()!=='')throw Error('No collection implicitly defaults to date');
    checks.push('no existing collection leaves target empty for explicit naming');
    return {checks,writesIntercepted:true};
  }finally{
    await page.evaluate(()=>localStorage.removeItem('image-upload-collection'));
    await page.goto('about:blank');
    await page.unroute('**/api/images/collections');await page.unroute('**/api/images?**');await page.unroute('**/api/image-uploads**');
  }
}
