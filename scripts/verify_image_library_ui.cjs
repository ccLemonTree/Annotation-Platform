async (page) => {
  const project='e251de70-5c16-46f4-9cd9-d90a2262539e',checks=[];
  let association,uploadURL;
  const associateRoute='http://localhost:3000/api/images/associate';
  const uploadRoute='http://localhost:3000/api/images/batch-upload';
  await page.route(associateRoute,async route=>{
    association=route.request().postDataJSON();
    await route.fulfill({json:{added_count:association.image_ids.length,asset_ids:[]}});
  });
  await page.route(uploadRoute,async route=>{
    uploadURL=route.request().url();
    await route.fulfill({status:201,json:[{id:'11111111-1111-4111-8111-111111111111'}]});
  });
  try {
    await page.setViewportSize({width:1440,height:1000});
    await page.goto('http://localhost:3000/assets');
    await page.getByText('独立图片库',{exact:true}).waitFor();
    await page.locator('.page-toolbar-actions').getByRole('button',{name:/上传图片/}).waitFor();
    await page.getByRole('row').filter({hasText:'20260417170218_25223_1840.jpg'}).waitFor();
    if(await page.getByRole('link',{name:'标注 / 审核',exact:true}).count())throw Error('Library unexpectedly bound to a project');
    checks.push('default library shows uploaded originals without choosing project');
    await page.getByRole('searchbox',{name:'筛选图片名称'}).fill('codex-no-image');
    await page.getByRole('searchbox',{name:'筛选图片名称'}).press('Enter');
    await page.getByText('暂无匹配图片，',{exact:false}).waitFor();
    checks.push('filename filter operates without project');
    await page.getByRole('searchbox',{name:'筛选图片名称'}).fill('20260417170218');
    await page.getByRole('searchbox',{name:'筛选图片名称'}).press('Enter');
    await page.getByRole('row').filter({hasText:'20260417170218_25223_1840.jpg'}).waitFor();
    await page.getByRole('row').filter({hasText:'20260417170218_25223_1840.jpg'}).getByRole('checkbox').check();
    await page.getByRole('button',{name:'添加到标注项目（1）',exact:true}).click();
    const modal=page.getByRole('dialog',{name:'添加到标注项目',exact:true});
    const target=modal.getByRole('combobox',{name:'图片添加到的项目'});
    await target.fill('test-img');await target.press('ArrowDown');await target.press('Enter');
    await modal.getByRole('button',{name:'添加图片',exact:true}).click();
    await modal.waitFor({state:'hidden'});
    if(association?.project_id!==project||association.image_ids.length!==1)throw Error('Selected image association failed');
    checks.push('selected catalog image is explicitly added to project');
    await page.locator('.page-toolbar-actions').getByRole('button',{name:/上传图片/}).click();
    const upload=page.getByRole('dialog',{name:'上传到图片库',exact:true});
    await upload.locator('input[type=file]').setInputFiles({name:'fixture.png',mimeType:'image/png',buffer:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==','base64')});
    await upload.getByRole('button',{name:'开始上传',exact:true}).click();
    await upload.waitFor({state:'hidden'});
    if(uploadURL!=='http://localhost:3000/api/images/batch-upload')throw Error('Upload unexpectedly requires project');
    checks.push('upload posts directly to library without project');
    await page.goto('http://localhost:3000/assets?project_id='+project);
    await page.getByRole('heading',{name:'项目图片',exact:true}).waitFor();
    await page.getByRole('link',{name:'标注 / 审核',exact:true}).first().waitFor();
    if(await page.getByRole('link',{name:'标注 / 审核',exact:true}).count()!==6)throw Error('Existing project uses changed');
    checks.push('legacy project references retain annotation entry points');
    await page.getByRole('button',{name:'从图片库添加',exact:true}).click();
    const picker=page.getByRole('dialog',{name:'从图片库添加到 test-img',exact:true});
    await picker.waitFor();
    await picker.getByRole('button',{name:'取消选图',exact:true}).click();
    await page.goto('http://localhost:3000/assets');
    checks.push('project picks another batch without leaving project');
    await page.setViewportSize({width:390,height:844});
    await page.waitForFunction(()=>parseInt(getComputedStyle(document.querySelector('.platform-main')).marginLeft)===64);
    if(await page.locator('html').evaluate(el=>el.scrollWidth>el.clientWidth+1))throw Error('Narrow library overflows');
    checks.push('library controls fit narrow viewport');
    await page.screenshot({path:'output/playwright/image-library-mobile.png'});
    await page.setViewportSize({width:1440,height:1000});
    await page.waitForFunction(()=>parseInt(getComputedStyle(document.querySelector('.platform-main')).marginLeft)===216);
    await page.screenshot({path:'output/playwright/image-library-desktop.png'});
    return {checks,scope:'real catalog and project reads; all upload/association writes intercepted'};
  }finally{await page.unroute(associateRoute);await page.unroute(uploadRoute);}
}
