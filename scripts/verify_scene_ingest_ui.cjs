async(page)=>{
  const settings='**/api/images/ingest-settings**',groupsUrl='http://localhost:3000/api/images/collections',checks=[];
  const base={classification_enabled:true,base_url:'http://model.test/v1',model:'Qwen3.8-27B',scenes:['工厂','河边'],min_long_edge:720,revision:1};
  const groups=[{id:'collection-a',name:'1013',image_count:6,ingest_config:{...base}},{id:'collection-b',name:'1014',image_count:0,ingest_config:{...base,min_long_edge:1080,scenes:['电梯']}}];
  const listing='http://localhost:3000/api/images?**';
  await page.goto('http://localhost:3000/assets');
  const headers={Authorization:'Bearer '+await page.evaluate(()=>localStorage.getItem('token'))};
  const original=await(await page.request.get('http://localhost:3000/api/images',{headers})).json();
  await page.route(listing,route=>route.fulfill({json:original}));
  let saved,uploadBody,savedCollection;
  await page.route(groupsUrl,async route=>{
    if(route.request().method()==='POST'){const group={id:'new-collection',name:route.request().postDataJSON().name,image_count:0,ingest_config:{...base}};groups.push(group);await route.fulfill({status:201,json:group});}
    else await route.fulfill({json:groups});
  });
  await page.route(settings,async route=>{
    if(route.request().url().endsWith('/test')){await route.fulfill({json:{models:['Qwen3.8-27B'],model_available:true}});return;}
    const id=new URL(route.request().url()).searchParams.get('collection_id'),group=groups.find(group=>group.id===id);
    if(route.request().method()==='PUT'){
      if(!group)throw Error('Settings save missed collection scope');
      saved=route.request().postDataJSON();savedCollection=id;group.ingest_config={...saved,revision:2};
    }
    await route.fulfill({json:group?.ingest_config||base});
  });
  const upload='http://localhost:3000/api/images/batch-upload';
  await page.route(upload,async route=>{uploadBody=route.request().postData();await route.fulfill({status:201,headers:{'X-Images-Skipped':'1'},json:[]});});
  try{
    await page.goto('http://localhost:3000/assets');
    if(!await page.getByRole('button',{name:'图片集设置',exact:true}).isDisabled())throw Error('No collection selected, yet settings enabled');
    const select=page.getByRole('combobox',{name:'筛选图片集',exact:true});
    await select.fill('1013');await select.press('ArrowDown');await select.press('Enter');
    await page.getByRole('button',{name:'图片集设置',exact:true}).click();
    const modal=page.getByRole('dialog',{name:'图片集入库与场景设置',exact:true});
    await modal.getByText(/当前图片集：1013/).waitFor();
    await modal.getByRole('textbox',{name:'模型服务地址',exact:true}).fill('http://custom-model.test/v1');
    const scenes=modal.getByRole('combobox',{name:'自定义场景',exact:true});
    await scenes.fill('高位摄像头');await scenes.press('Enter');await scenes.press('Tab');
    await modal.getByRole('spinbutton',{name:'默认长边阈值',exact:true}).click();
    await modal.getByRole('button',{name:'连接测试',exact:true}).click();
    await modal.getByText('模型服务已连接，配置的模型可用',{exact:true}).waitFor();
    await modal.getByRole('button',{name:'保存设置',exact:true}).click();await modal.waitFor({state:'hidden'});
    if(savedCollection!=='collection-a'||saved.base_url!=='http://custom-model.test/v1'||!saved.scenes.includes('高位摄像头'))throw Error('Scoped changes lost');
    if(groups[1].ingest_config.min_long_edge!==1080||groups[1].ingest_config.base_url!==base.base_url)throw Error('Other collection changed');
    checks.push('collection settings save target ID; another collection remains independent');
    await select.fill('1014');await select.press('ArrowDown');await select.press('Enter');
    await page.getByRole('button',{name:'图片集设置',exact:true}).click();
    if(await modal.getByRole('spinbutton',{name:'默认长边阈值',exact:true}).inputValue()!=='1080')throw Error('Second collection policy not loaded');
    await modal.getByRole('button',{name:'关闭',exact:true}).click();
    await select.fill('1013');await select.press('ArrowDown');await select.press('Enter');
    checks.push('switching collections loads the independently saved threshold');
    await page.locator('.page-toolbar-actions').getByRole('button',{name:/上传图片/}).click();
    const dialog=page.getByRole('dialog',{name:'上传到图片库',exact:true});
    await dialog.getByText(/图片集规则：长边 ≥ 720/).waitFor();
    await dialog.getByRole('spinbutton',{name:'本次上传长边阈值',exact:true}).fill('1080');
    await dialog.locator('input[type=file]').setInputFiles({name:'fixture.png',mimeType:'image/png',buffer:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==','base64')});
    await dialog.getByRole('button',{name:'开始上传',exact:true}).click();await dialog.waitFor({state:'hidden'});
    if(!uploadBody.includes('name="min_long_edge"')||!uploadBody.includes('1080')||!uploadBody.includes('1013'))throw Error('Upload missed target/policy');
    checks.push('upload selects collection and only overrides threshold for this batch');
    await page.getByRole('button',{name:'新建图片集',exact:true}).click();
    const create=page.getByRole('dialog',{name:'新建图片集',exact:true});
    await create.getByRole('textbox',{name:'新图片集名称',exact:true}).fill('empty-new');
    await create.getByRole('button',{name:/创.*建/,exact:true}).click();await create.waitFor({state:'hidden'});
    await page.locator('p.workspace-help:visible').filter({hasText:'当前图片集：empty-new'}).waitFor();
    if(!groups.some(group=>group.name==='empty-new'))throw Error('Empty collection missing');
    checks.push('create empty collection before upload so policy can be configured first');
    return {checks,scope:'all configuration/upload/creation writes intercepted'};
  }finally{await page.unroute(listing);await page.unroute(settings);await page.unroute(groupsUrl);await page.unroute(upload);}
}
