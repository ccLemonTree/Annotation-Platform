async page => {
  await page.addInitScript({path:'F:/hzxf/数据管理平台/backend/backend/.backups/multimodal-runtime/browser-auth.js'});
  const errors=[],writes=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.bringToFront();await page.unrouteAll();await page.emulateMedia({reducedMotion:'no-preference'});
  await page.route(/^http:\/\/localhost:3000\/api\//,route=>{
    if(route.request().method()!=='GET'){writes.push(route.request().url());return route.fulfill({status:405,json:{detail:'Read only verification'}});}
    return route.continue();
  });
  await page.goto('http://localhost:3000/login');
  const state=await page.evaluate(()=>window.__multimodalSmoke);
  await page.setViewportSize({width:1440,height:1000});await page.goto('http://localhost:3000/annotations?project_id='+state.projects.vector);
  await page.getByRole('combobox',{name:'历史匹配查询'}).click();
  await page.locator('.ant-select-dropdown:visible').getByText(/黑色和灰色的图形/).click();
  await page.keyboard.press('Escape');
  await page.getByText(/当前查询：黑色和灰色的图形/).waitFor();
  if(await page.getByRole('radiogroup',{name:/判定 /}).count()!==2)throw Error('Real query results missing');
  await page.screenshot({path:'output/playwright/multimodal-live-vector-desktop.png',fullPage:true});
  await page.setViewportSize({width:390,height:844});
  await page.waitForFunction(()=>document.documentElement.scrollWidth<=innerWidth);
  if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth))throw Error('Live mobile overflow');
  await page.screenshot({path:'output/playwright/multimodal-live-vector-mobile.png',fullPage:true});
  await page.setViewportSize({width:1440,height:1000});await page.goto('http://localhost:3000/annotate/'+state.assets.classification[0]);
  await page.getByText('v1 · 已通过',{exact:true}).waitFor();
  await page.getByRole('radiogroup',{name:'图片分类',exact:true}).getByRole('radio',{name:'yes',exact:true}).waitFor();
  await page.screenshot({path:'output/playwright/multimodal-live-classification.png'});
  if(writes.length||errors.length)throw Error(JSON.stringify({writes,errors}));
  return {realQueryHits:2,realClassificationReview:'approved',widths:[1440,390],writes:0,pageErrors:errors};
}
