async page=>{
  await page.unrouteAll();page.setDefaultTimeout(15000);
  const origin='http://192.168.1.90:3000',bootstrap=origin+'/__annotation-shortcut-auth',pattern=/^http:\/\/192\.168\.1\.90:3000\/api\//;
  await page.route(bootstrap,route=>route.fulfill({path:'F:/hzxf/数据管理平台/output/.backups/navigation-browser-auth.json',contentType:'application/json'}));
  await page.goto(bootstrap);const auth=JSON.parse(await page.locator('body').innerText());await page.unroute(bootstrap);await page.goto(origin+'/login');
  const previous=await page.evaluate(token=>{const old=localStorage.getItem('token');localStorage.setItem('token',token);return old;},auth.token);
  const writes=[],checks=[];
  await page.route(pattern,route=>{if(route.request().method()!=='GET'){writes.push(route.request().url());return route.abort();}return route.continue();});
  try{
    await page.setViewportSize({width:1440,height:1000});
    await page.goto(origin+'/annotate/project/e00b28ee-2289-4c8e-91ae-7e0c91e8a24e?mode=text&query_id=8854337f-5f04-4144-9eb9-2d530b5f7711');
    const active=()=>page.locator('[data-match-active="true"]');await active().waitFor();
    const first=await active().getAttribute('id');await page.evaluate(()=>document.activeElement?.blur());await page.keyboard.press('ArrowRight');
    await page.waitForFunction(first=>document.querySelector('[data-match-active="true"]')?.id!==first,first);
    const asset=(await active().getAttribute('id')).slice('match-'.length);
    await page.getByRole('button',{name:'快捷键',exact:true}).click();
    const help=page.getByRole('dialog',{name:'向量标注快捷键',exact:true});await help.waitFor();
    await page.keyboard.press('1');if(writes.length)throw Error('Modal shortcut attempted a real judgment');
    await page.screenshot({path:'output/playwright/vector-shortcuts-live.png',animations:'disabled'});
    await page.keyboard.press('Escape');await help.waitFor({state:'hidden'});
    checks.push('Existing vector query selects the next real result and exposes shortcut help without saving judgments');
    await page.goto(origin+'/annotate/'+asset+'?from=vector&mode=description');
    await page.getByLabel('图片描述',{exact:true}).waitFor();
    await page.getByRole('button',{name:'快捷键',exact:true}).click();
    const captionHelp=page.getByRole('dialog',{name:'标注快捷键',exact:true});await captionHelp.waitFor();
    await page.screenshot({path:'output/playwright/caption-shortcuts-live.png',animations:'disabled'});
    await page.keyboard.press('Escape');await captionHelp.waitFor({state:'hidden'});await page.evaluate(()=>document.activeElement?.blur());
    const key=await page.getByRole('link',{name:'下一张',exact:true}).count()?'ArrowRight':'ArrowLeft';
    await page.keyboard.press(key);await page.waitForFunction(asset=>!location.pathname.endsWith('/'+asset),asset);
    await page.getByLabel('图片描述',{exact:true}).waitFor();
    if(!page.url().includes('from=vector')||!page.url().includes('mode=description'))throw Error('Description navigation lost context');
    checks.push('Existing vector image description supports arrow navigation and shortcut help with return context');
    await page.setViewportSize({width:390,height:844});await page.screenshot({path:'output/playwright/caption-shortcuts-live-mobile.png',fullPage:true,animations:'disabled'});
    if(writes.length)throw Error('Unexpected write attempts '+writes.length);
    return {checks,businessWrites:0,modelRequests:0};
  }finally{await page.goto(origin+'/login');await page.evaluate(previous=>{if(previous)localStorage.setItem('token',previous);else localStorage.removeItem('token');},previous);await page.goto('about:blank');await page.unrouteAll({behavior:'ignoreErrors'});}
}
