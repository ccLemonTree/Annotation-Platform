async page=>{
  await page.unrouteAll();page.setDefaultTimeout(15000);
  const origin='http://192.168.1.90:3000',bootstrap=origin+'/__navigation-test-auth',pattern=/^http:\/\/192\.168\.1\.90:3000\/api\//;
  await page.route(bootstrap,route=>route.fulfill({path:'F:/hzxf/数据管理平台/output/.backups/navigation-browser-auth.json',contentType:'application/json'}));
  await page.goto(bootstrap);const auth=JSON.parse(await page.locator('body').innerText());await page.unroute(bootstrap);
  await page.goto(origin+'/login');
  const previous=await page.evaluate(token=>{const previous=localStorage.getItem('token');localStorage.setItem('token',token);return previous;},auth.token);
  const writes=[],checks=[];
  await page.route(pattern,route=>{if(route.request().method()!=='GET'){writes.push(route.request().url());return route.abort();}return route.continue();});
  const loaded=()=>page.waitForFunction(()=>{const img=document.querySelector('.model-preview-image img');return img?.complete&&img.naturalWidth>0;});
  try{
    const headers={Authorization:'Bearer '+auth.token};
    const previews=await(await page.request.get(origin+'/api/model-previews',{headers})).json();
    const preview=previews.items.find(row=>row.status==='completed'&&row.total>=3);if(!preview)throw Error('No completed preview with at least 3 images');
    const contract=await(await page.request.get('http://127.0.0.1:8000/openapi.json')).json();
    if(contract.components.schemas.ValidationConfig.properties.target_overlap.default!==.2)throw Error('Live backend missing saved overlap configuration');
    await page.setViewportSize({width:1600,height:800});await page.goto(origin+'/model-preview?run_id='+preview.id);
    await loaded();await page.locator('.model-preview-gallery .ant-btn').nth(1).click();await loaded();
    await page.evaluate(()=>{document.activeElement?.blur();const image=document.querySelector('.model-preview-image');window.scrollTo(0,Math.min(image.getBoundingClientRect().top+scrollY-70,document.documentElement.scrollHeight-innerHeight-200));window.scrollSamples=[scrollY];window.scrollTimer=setInterval(()=>window.scrollSamples.push(scrollY),10);});
    const before=await page.locator('.model-preview-gallery .is-selected').textContent();await page.keyboard.press('ArrowRight');
    await page.waitForFunction(before=>document.querySelector('.model-preview-gallery .is-selected')?.textContent!==before,before);await loaded();
    const samples=await page.evaluate(()=>{clearInterval(window.scrollTimer);return window.scrollSamples;});
    if(samples[0]<100||Math.max(...samples)-Math.min(...samples)>2)throw Error('Actual preview scroll moved '+JSON.stringify(samples));
    await page.getByText(/共 11 张图片 · 第 1 \/ 1 页/).waitFor();
    await page.screenshot({path:'output/playwright/preview-scroll-live.png',animations:'disabled'});
    checks.push('Actual preview arrow navigation retains vertical position through image fetch; explicit page count visible');
    await page.goto(origin+'/validation');await page.getByRole('button',{name:'验证权重',exact:true}).first().click();
    const dialog=page.getByRole('dialog',{name:'验证训练权重',exact:true}),input=dialog.getByRole('spinbutton',{name:'任务交集阈值（%）',exact:true});
    await input.waitFor();if(await input.inputValue()!=='20')throw Error('Creation form default is not 20 percent');
    await input.fill('35');await input.press('Tab');
    await dialog.getByText('标准评测匹配 IoU',{exact:true}).waitFor();
    await page.screenshot({path:'output/playwright/validation-overlap-form-live.png',animations:'disabled'});
    await dialog.getByRole('button',{name:/^取\s*消$/}).click();
    checks.push('Actual creation dialog exposes independent overlap percent and standard IoU; canceled without submission');
    if(writes.length)throw Error('Unexpected write attempts '+writes.length);
    return {checks,previewImages:preview.total,verticalScroll:samples[0],maxVerticalShift:Math.max(...samples)-Math.min(...samples),newInference:0,businessWrites:0};
  }finally{
    await page.goto(origin+'/login');await page.evaluate(previous=>{if(previous)localStorage.setItem('token',previous);else localStorage.removeItem('token');},previous);
    await page.goto('about:blank');await page.unrouteAll();
  }
}
