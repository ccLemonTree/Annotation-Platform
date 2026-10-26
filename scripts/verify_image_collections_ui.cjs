async(page)=>{
  const checks=[];let uploadBody='',uploadType='',assignment;
  const uploadRoute='http://localhost:3000/api/images/batch-upload',assignRoute='http://localhost:3000/api/images/collections/assign';
  await page.route(uploadRoute,async route=>{
    uploadType=route.request().headers()['content-type']||'';
    uploadBody=(route.request().postDataBuffer()||Buffer.alloc(0)).toString('utf8');
    await route.fulfill({status:201,json:[{id:'11111111-1111-4111-8111-111111111111'}]});
  });
  await page.route(assignRoute,async route=>{
    assignment=route.request().postDataJSON();
    await route.fulfill({json:{collection_id:collection.id,added_count:assignment.image_ids.length}});
  });
  let collection;
  try{
    await page.setViewportSize({width:1440,height:1000});
    await page.goto('http://localhost:3000/assets');
    await page.getByRole('row').filter({hasText:'20260417170218_25223_1840.jpg'}).waitFor();
    const response=await page.request.get('http://localhost:3000/api/images/collections',{headers:{Authorization:'Bearer '+await page.evaluate(()=>localStorage.getItem('token'))}});
    if(response.status()!==200)throw Error('Live collection read failed');
    collection=(await response.json()).find(group=>group.name==='历史图片');
    if(!collection||collection.image_count!==6)throw Error('Historical collection mismatch');
    const filter=page.getByRole('combobox',{name:'筛选图片集'});
    await filter.fill('历史图片');const filtered=page.waitForResponse(response=>response.url().includes('collection_id='+collection.id));
    await filter.press('ArrowDown');await filter.press('Enter');await filtered;
    checks.push('named collection filter reads six historical originals');
    await page.getByRole('row').filter({hasText:'20260417170218_25223_1840.jpg'}).getByRole('checkbox').check();
    await page.getByRole('button',{name:'加入图片集（1）',exact:true}).click();
    const group=page.getByRole('dialog',{name:'加入图片集',exact:true});
    await group.getByRole('combobox',{name:'归类图片集名称'}).fill('1014');
    await group.getByRole('button',{name:'加 入',exact:true}).click();
    await group.waitFor({state:'hidden'});
    if(assignment.name!=='1014'||assignment.image_ids.length!==1)throw Error('Grouping selection lost');
    checks.push('existing selected image can join a named collection');
    await page.locator('.page-toolbar-actions').getByRole('button',{name:/上传图片/}).click();
    const upload=page.getByRole('dialog',{name:'上传到图片库',exact:true});
    await upload.getByRole('combobox',{name:'上传图片集名称'}).fill('1013');
    await upload.locator('input[type=file]').setInputFiles({name:'collection.png',mimeType:'image/png',buffer:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAABQAAAAUCAIAAAAC64paAAAAG0lEQVR4nGP8z0A+YKJA76jmUc2jmkc1U0EzACKcASc1hNCeAAAAAElFTkSuQmCC','base64')});
    await upload.getByRole('button',{name:'开始上传',exact:true}).click();await upload.waitFor({state:'hidden'});
    if(!uploadType.startsWith('multipart/form-data; boundary=')||!uploadBody.includes('filename="collection.png"')||!uploadBody.includes('Content-Type: image/png')||!uploadBody.includes('name="collection_name"')||!uploadBody.includes('1013'))throw Error('Collection upload name missing: '+JSON.stringify({length:uploadBody.length,fields:uploadBody.match(/name="[^"]+"/g),hasExpectedValue:uploadBody.includes('1013')}));
    checks.push('upload submits collection name and image bytes with real multipart boundary');
    await page.setViewportSize({width:390,height:844});
    await page.waitForFunction(()=>parseInt(getComputedStyle(document.querySelector('.platform-main')).marginLeft)===64);
    if(await page.locator('html').evaluate(el=>el.scrollWidth>el.clientWidth+1))throw Error('Collection controls overflow');
    checks.push('named collection controls fit narrow viewport');
    await page.screenshot({path:'output/playwright/image-collections-mobile.png'});
    await page.setViewportSize({width:1440,height:1000});
    await page.waitForFunction(()=>parseInt(getComputedStyle(document.querySelector('.platform-main')).marginLeft)===216);
    await page.screenshot({path:'output/playwright/image-collections-desktop.png'});
    return {checks,scope:'real six historical originals read; upload/classification writes intercepted'};
  }finally{await page.unroute(uploadRoute);await page.unroute(assignRoute);}
}
