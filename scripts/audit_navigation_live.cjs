async page => {
  const writes=[],errors=[];
  await page.unrouteAll();
  await page.route(/^http:\/\/localhost:3000\/api\//, route => {
    if(route.request().method()!=='GET'){writes.push(route.request().url());return route.abort();}
    return route.continue();
  });
  page.on('pageerror',e=>errors.push(e.message));
  const routes=['/','/assets','/projects','/annotations','/automation','/models','/training','/validation','/analytics','/gpu-resources','/gpu-terminal','/storage-maintenance','/smarteye','/image-semantics/history'];
  const result=[];
  for(const path of routes){
    await page.goto('http://localhost:3000'+path);
    await page.getByRole('heading',{level:1}).waitFor();
    await page.waitForTimeout(650);
    result.push({path,title:await page.locator('h1').innerText(),actions:await page.locator('main button, .ant-layout-content button, .ant-layout-content a').evaluateAll(nodes=>nodes.slice(0,24).map(n=>({text:n.textContent?.trim(),href:n.getAttribute('href')})))});
  }
  await page.setViewportSize({width:1680,height:1100});
  await page.goto('http://localhost:3000/annotations');
  const projectLink=page.getByRole('link',{name:'审核标注',exact:true}).first();
  await projectLink.waitFor();
  if(await projectLink.count()){
    const href=await projectLink.getAttribute('href');await projectLink.click();await page.waitForTimeout(600);
    result.push({path:href,actions:await page.locator('.ant-layout-content a').evaluateAll(nodes=>nodes.map(n=>({text:n.textContent,href:n.getAttribute('href')})))});
    await page.screenshot({path:'output/playwright/navigation-review-live.png',fullPage:true});
    const projectId=new URL('http://localhost:3000'+href).searchParams.get('project_id');
    for(const stage of ['images','prepare','deliver']){
      await page.goto(`http://localhost:3000/projects/${projectId}/${stage}`);
      await page.getByRole('navigation',{name:'项目导航',exact:true}).waitFor();
      await page.waitForTimeout(400);
      result.push({path:page.url(),current:await page.locator('[aria-label="项目导航"] [aria-current]').innerText()});
      if(stage==='deliver')await page.screenshot({path:'output/playwright/navigation-deliver-live.png',fullPage:true});
    }
  }
  return {result,writes,errors};
}
