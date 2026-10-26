async(page)=>{
  const id='e251de70-5c16-46f4-9cd9-d90a2262539e',path='/projects/'+id+'/images';
  const checks=[],headers={Authorization:'Bearer '+await page.evaluate(()=>localStorage.getItem('token'))};
  const originalAssets=(await (await page.request.get('http://localhost:3000/api/assets?project_id='+id,{headers})).json()).items;
  const originals=(await (await page.request.get('http://localhost:3000/api/images',{headers})).json()).items;
  const imageId='11111111-1111-4111-8111-111111111111',assetId='22222222-2222-4222-8222-222222222222';
  const extra={...originals[0],id:imageId,original_name:'navigation-fixture.jpg'};
  let records=[...originalAssets],fail=false,writes=0,payload;
  const patterns=['http://localhost:3000/api/images?**','http://localhost:3000/api/assets?**',
    'http://localhost:3000/api/images/associate','http://localhost:3000/api/images/'+imageId+'/content',
    'http://localhost:3000/api/assets/'+assetId+'/content'];
  await page.route(patterns[0],async route=>{
    const q=new URL(route.request().url()).searchParams.get('q')||'';
    const items=[...originals,extra].filter(image=>image.original_name.includes(q));
    await route.fulfill({json:{items,total:items.length,page:1,page_size:20}});
  });
  await page.route(patterns[1],async route=>{
    if(new URL(route.request().url()).searchParams.get('project_id')!==id){await route.continue();return;}
    await route.fulfill({json:{items:records,total:records.length,page:1,page_size:100}});
  });
  await page.route(patterns[2],async route=>{
    writes++;payload=route.request().postDataJSON();
    if(fail){await route.fulfill({status:500,json:{detail:'navigation fixture failure'}});return;}
    const added=payload.image_ids.includes(imageId)&&!records.some(asset=>asset.image_id===imageId);
    if(added)records.push({...originalAssets[0],id:assetId,image_id:imageId,original_name:extra.original_name,annotation_status:'pending'});
    await route.fulfill({json:{added_count:added?1:0,asset_ids:[assetId]}});
  });
  for(const [pattern,realPath] of [[patterns[3],'/api/images/'+originals[0].id+'/content'],[patterns[4],'/api/assets/'+originalAssets[0].id+'/content']])
    await page.route(pattern,async route=>await route.fulfill({response:await route.fetch({url:'http://localhost:3000'+realPath})}));
  try{
    await page.setViewportSize({width:1440,height:1000});
    await page.goto('http://localhost:3000/projects');
    const row=page.getByRole('row').filter({has:page.getByText('test-img',{exact:true})});
    await row.getByRole('link',{name:'管理图片',exact:true}).click();
    await page.waitForURL('http://localhost:3000'+path);
    await page.getByRole('heading',{name:'项目图片',exact:true}).waitFor();
    if(!await page.getByRole('menuitem',{name:'项目管理',exact:true}).evaluate(el=>el.classList.contains('ant-menu-item-selected')))throw Error('Wrong active section');
    checks.push('project has explicit URL, title and active project section');
    await page.getByRole('button',{name:'从图片库添加',exact:true}).click();
    const picker=page.getByRole('dialog',{name:'从图片库添加到 test-img',exact:true});
    await picker.waitFor();
    if(new URL(page.url()).pathname!==path)throw Error('Picker navigated away from project');
    checks.push('picker keeps project context and location');
    await picker.getByText('已在当前项目',{exact:true}).first().waitFor();
    const used=picker.getByRole('row').filter({hasText:originals[0].original_name});
    if(!await used.getByRole('checkbox').isDisabled())throw Error('Used image can be reselected');
    checks.push('already used images are marked and disabled');
    await picker.getByRole('row').filter({hasText:extra.original_name}).getByRole('checkbox').check();
    fail=true;
    await picker.getByRole('button',{name:'添加到 test-img',exact:true}).click();
    await picker.getByText('添加失败，选择已保留，请重试',{exact:true}).waitFor();
    if(new URL(page.url()).pathname!==path||!await picker.getByRole('row').filter({hasText:extra.original_name}).getByRole('checkbox').isChecked())throw Error('Failed add lost context/selection');
    checks.push('failed add preserves selected images and project');
    fail=false;await picker.getByRole('button',{name:'重试添加',exact:true}).click();
    await picker.waitFor({state:'hidden'});
    await page.getByRole('row').filter({hasText:extra.original_name}).waitFor();
    if(new URL(page.url()).pathname!==path||payload.project_id!==id||writes!==2)throw Error('Successful add changed destination or duplicated writes');
    checks.push('retry adds once and stays in project');
    await page.getByRole('button',{name:'从图片库添加',exact:true}).click();
    await picker.waitFor();await picker.getByRole('button',{name:'取消选图',exact:true}).click();await picker.waitFor({state:'hidden'});
    if(writes!==2||new URL(page.url()).pathname!==path)throw Error('Cancel wrote data or navigated');
    checks.push('cancel closes picker without write or navigation');
    await page.getByRole('row').filter({hasText:originalAssets[0].original_name}).getByRole('link',{name:'标注 / 审核',exact:true}).click();
    await page.getByRole('img',{name:'检测画框区域'}).waitFor();
    await page.getByRole('link',{name:'返回 test-img 项目图片',exact:true}).click();
    await page.waitForURL('http://localhost:3000'+path);
    checks.push('annotation returns to the same project images');
    await page.goto('http://localhost:3000/assets?project_id='+id);
    await page.waitForURL('http://localhost:3000'+path);
    checks.push('old project links redirect to canonical project page');
    await page.reload();await page.getByRole('heading',{name:'项目图片',exact:true}).waitFor();
    checks.push('refresh retains project context');
    await page.goto('http://localhost:3000/assets');
    await page.getByText('独立图片库',{exact:true}).waitFor();
    if(await page.getByRole('combobox',{name:'图片查看范围'}).count())throw Error('Library still switches into projects');
    checks.push('library has a single purpose without project switching');
    await page.getByRole('row').filter({hasText:extra.original_name}).getByRole('checkbox').check();
    await page.getByRole('button',{name:'添加到标注项目（1）',exact:true}).click();
    const modal=page.getByRole('dialog',{name:'添加到标注项目',exact:true});
    const target=modal.getByRole('combobox',{name:'图片添加到的项目'});
    await target.fill('test-img');await target.press('ArrowDown');await target.press('Enter');
    if(await modal.getByRole('link').count())throw Error('Modal allows navigation before completion');
    await modal.getByRole('button',{name:'添加图片',exact:true}).click();await modal.waitFor({state:'hidden'});
    const next=page.getByRole('link',{name:'查看 test-img 项目图片',exact:true});
    await next.waitFor();
    if(new URL(page.url()).pathname!=='/assets')throw Error('Library auto-navigated without user action');
    checks.push('library add keeps location and offers explicit next link');
    await next.click();await page.waitForURL('http://localhost:3000'+path);
    await page.goBack();await page.getByText('独立图片库',{exact:true}).waitFor();
    checks.push('explicit next step and browser back have clear destinations');
    await page.goto('http://localhost:3000'+path);
    await page.setViewportSize({width:390,height:844});
    await page.waitForFunction(()=>parseInt(getComputedStyle(document.querySelector('.platform-main')).marginLeft)===64);
    if(await page.locator('html').evaluate(el=>el.scrollWidth>el.clientWidth+1))throw Error('Project page overflows');
    await page.getByRole('button',{name:'从图片库添加',exact:true}).click();await picker.waitFor();
    if(await page.locator('html').evaluate(el=>el.scrollWidth>el.clientWidth+1))throw Error('Picker overflows');
    checks.push('project page and picker fit narrow viewport');
    await page.screenshot({path:'output/playwright/navigation-picker-mobile.png'});
    await picker.getByRole('button',{name:'取消选图',exact:true}).click();
    await page.setViewportSize({width:1440,height:1000});
    await page.waitForFunction(()=>parseInt(getComputedStyle(document.querySelector('.platform-main')).marginLeft)===216);
    await page.screenshot({path:'output/playwright/navigation-project-desktop.png'});
    return {checks,scope:'real projects/images read; new library image, project use and all association writes intercepted'};
  }finally{for(const pattern of patterns)await page.unroute(pattern);}
}

