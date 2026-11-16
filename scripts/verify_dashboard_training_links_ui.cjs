async page=>{
  const base='http://127.0.0.1:3000', project='11111111-1111-4111-8111-111111111111';
  await page.addInitScript(()=>{localStorage.setItem('token','isolated-test');localStorage.setItem('user',JSON.stringify({role:'admin'}));});
  await page.route(base+'/api/**',async route=>{
    const path=new URL(route.request().url()).pathname;
    if(path==='/api/auth/me')return route.fulfill({json:{id:1,tenant_id:1,username:'管理员',role:'admin'}});
    if(path==='/api/training/runs')return route.fulfill({json:{items:[{id:'p',name:'项目训练',status:'running',created_at:'2026-11-01T12:00:00Z',source:{project_id:project}},{id:'l',name:'旧训练',status:'running',created_at:'2026-11-01T11:00:00Z',source:{}}],total:2}});
    if(path==='/api/image-semantics/jobs'||path==='/api/gpu-resources/servers')return route.fulfill({json:[]});
    return route.fulfill({json:{items:[],total:0,projects_overview:{total:0},assets_overview:{total:0,project_references:0},annotations_overview:{total:0,approved:0,pending_review:0}}});
  });
  await page.goto(base+'/');
  const projectLink=page.locator('.dashboard-queue-row').filter({hasText:'项目训练'});await projectLink.waitFor();
  if(await projectLink.getAttribute('href')!==`/projects/${project}/deliver`)throw Error('项目训练没有链接到来源项目');
  if(await page.locator('.dashboard-queue-row').filter({hasText:'旧训练'}).getAttribute('href')!=='/projects')throw Error('无项目任务没有链接到项目选择');
  return {projectLink:true,legacyLink:true};
}
