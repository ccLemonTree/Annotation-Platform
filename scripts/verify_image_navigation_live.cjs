async page=>{
  await page.unrouteAll();page.setDefaultTimeout(20000);
  const authPath='F:/hzxf/数据管理平台/output/.backups/navigation-browser-auth.json';
  const origin='http://192.168.1.90:3000',checks=[],writes=[],pattern=/^http:\/\/192\.168\.1\.90:3000\/api\//;
  await page.route(pattern,route=>{if(route.request().method()!=='GET'){writes.push(route.request().url());return route.abort();}return route.continue();});
  const blur=()=>page.evaluate(()=>document.activeElement?.blur());
  const choose=async(name,text)=>{await page.getByRole('combobox',{name,exact:true}).click();await page.locator('.ant-select-dropdown:visible').getByText(text,{exact:true}).last().click();};
  const bootstrap=origin+'/__navigation-test-auth';
  await page.route(bootstrap,route=>route.fulfill({path:authPath,contentType:'application/json'}));
  await page.goto(bootstrap);const auth=JSON.parse(await page.locator('body').innerText());await page.unroute(bootstrap);
  await page.goto(origin+'/login');
  const previousToken=await page.evaluate(token=>{const previous=localStorage.getItem('token');localStorage.setItem('token',token);return previous;},auth.token);
  try{
    await page.setViewportSize({width:1600,height:1000});
    await page.goto(origin+'/model-preview?run_id=2e5ce158-a4d8-482d-aad7-2ad3f6dd38e8');
    const gallery=page.locator('.model-preview-gallery'),selected=gallery.locator('.is-selected');
    await selected.waitFor();const first=await selected.textContent();await blur();
    await page.waitForFunction(()=>{const img=document.querySelector('.model-preview-image img');return img?.complete&&img.naturalWidth>0;});
    await page.keyboard.press('ArrowRight');await page.waitForFunction(first=>document.querySelector('.model-preview-gallery .is-selected')?.textContent!==first,first);
    await page.waitForFunction(()=>{const img=document.querySelector('.model-preview-image img');return img?.complete&&img.naturalWidth>0;});
    await page.keyboard.press('ArrowLeft');await page.waitForFunction(first=>document.querySelector('.model-preview-gallery .is-selected')?.textContent===first,first);
    await page.waitForFunction(()=>{const img=document.querySelector('.model-preview-image img');return img?.complete&&img.naturalWidth>0;});
    await page.screenshot({path:'output/playwright/navigation-preview-live.png',animations:'disabled'});
    checks.push('Actual 11-image checkpoint preview switches right and left using saved results');
    await page.goto(origin+'/validation');
    const run=page.getByRole('row').filter({hasText:'烟火验证集'}).filter({has:page.getByRole('button',{name:'查看结果',exact:true})});
    await run.first().getByRole('button',{name:'查看结果',exact:true}).click();
    const threshold=page.getByRole('spinbutton',{name:'交集阈值（%）',exact:true});await threshold.waitFor();
    if(await threshold.inputValue()!=='20')throw Error('Default overlap threshold was not 20');
    await choose('错误类型','漏检');await choose('错误标签','fire');
    await page.getByRole('button',{name:'查看叠框',exact:true}).first().click();
    const dialog=page.getByRole('dialog');await dialog.getByRole('img',{name:'真值与预测叠框',exact:true}).waitFor();
    const filename=await dialog.locator('img').getAttribute('alt');await page.keyboard.press('ArrowRight');
    await page.waitForFunction(filename=>document.querySelector('.ant-modal-body img')?.getAttribute('alt')!==filename,filename);
    await dialog.getByRole('img',{name:'真值与预测叠框',exact:true}).waitFor();
    await page.keyboard.press('ArrowLeft');await dialog.getByRole('img',{name:filename,exact:true}).waitFor();
    await page.screenshot({path:'output/playwright/navigation-validation-live.png',animations:'disabled'});
    checks.push('Actual validation modal switches within fire misses under 20% intersection policy');
    if(writes.length)throw Error('Unexpected write attempts '+writes.length);
    return {checks,businessWrites:0,newInference:0};
  }catch(error){await page.screenshot({path:'output/playwright/navigation-live-error.png',animations:'disabled'});throw Error(error.message+'; url '+page.url()+'; body '+(await page.locator('body').innerText()).slice(-700));}
  finally{await page.goto(origin+'/login');await page.evaluate(previous=>{if(previous)localStorage.setItem('token',previous);else localStorage.removeItem('token');},previousToken);await page.goto('about:blank');await page.unroute(pattern);}
}
