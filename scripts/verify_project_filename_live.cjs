async page=>{
  await page.unrouteAll();page.setDefaultTimeout(15000);
  const origin='http://192.168.1.90:3000',bootstrap=origin+'/__project-filename-auth',pattern=/^http:\/\/192\.168\.1\.90:3000\/api\//;
  await page.route(bootstrap,route=>route.fulfill({path:'F:/hzxf/数据管理平台/output/.backups/project-filename-browser.json',contentType:'application/json'}));
  await page.goto(bootstrap);const auth=JSON.parse(await page.locator('body').innerText());await page.unroute(bootstrap);await page.goto(origin+'/login');
  const previous=await page.evaluate(token=>{const old=localStorage.getItem('token');localStorage.setItem('token',token);return old;},auth.token);
  const calls=[],writes=[],checks=[];
  await page.route(pattern,route=>{const req=route.request();if(req.method()!=='GET'){writes.push(req.url());return route.abort();}calls.push(req.url());return route.continue();});
  try{
    await page.setViewportSize({width:1600,height:1000});await page.goto(origin+'/projects/'+auth.project_id+'/images');
    await page.getByText('共 '+auth.total+' 张',{exact:true}).first().waitFor();
    const input=page.getByLabel('项目图片文件名',{exact:true});await input.fill('D1780989175833');await input.press('Enter');
    await page.getByText('共 1 张',{exact:true}).first().waitFor();await page.getByText('D1780989175833_20260616144237.jpeg',{exact:true}).waitFor();
    await page.screenshot({path:'output/playwright/project-filename-live.png',animations:'disabled'});
    checks.push('Actual 3945-image project narrows to the requested filename');
    await input.fill('JPEG');await input.press('Enter');await page.getByText('共 1620 张',{exact:true}).first().waitFor();
    await page.getByTitle('2',{exact:true}).first().click();
    await page.waitForFunction(()=>document.querySelector('.ant-pagination-item-active')?.getAttribute('title')==='2');
    await input.fill('D1780989175833');await page.getByRole('button',{name:/^搜\s*索$/}).click();
    await page.getByText('共 1 张',{exact:true}).first().waitFor();
    if(!calls.some(url=>{const q=new URL(url).searchParams;return q.get('q')==='JPEG'&&q.get('page')==='2';}))throw Error('Live paging lost search');
    await input.fill('');await page.getByText('共 '+auth.total+' 张',{exact:true}).first().waitFor();
    checks.push('Case-insensitive search, paging, search-button reset and clearing use the real backend');
    await page.setViewportSize({width:390,height:844});await input.fill('D1780989175833');await input.press('Enter');
    await page.getByText('共 1 张',{exact:true}).first().waitFor();
    await page.screenshot({path:'output/playwright/project-filename-live-mobile.png',animations:'disabled',fullPage:true});
    if(writes.length)throw Error('Unexpected writes '+writes.length);
    return {checks,businessWrites:0,existingImagesChanged:0};
  }finally{await page.goto(origin+'/login');await page.evaluate(previous=>{if(previous)localStorage.setItem('token',previous);else localStorage.removeItem('token');},previous);await page.goto('about:blank');await page.unrouteAll({behavior:'ignoreErrors'});}
}
