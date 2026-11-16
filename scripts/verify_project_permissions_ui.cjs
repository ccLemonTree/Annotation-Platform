async page=>{
  page.setDefaultTimeout(12000);
  const ensure=(ok,label)=>{if(!ok)throw Error(label);checks.push(label);},checks=[],unexpected=[],writes=[],reads=[];
  let role='user',assigned=[2],empty=false,saveError=false;
  const project={id:'11111111-1111-4111-8111-111111111111',project_name:'隔离分配项目',task_type:'vector',status:'active',created_at:'2026-11-02T00:00:00Z',label_schema:{labels:[]},total_assets:1,annotated_count:0,reviewed_count:0};
  const user=(id,username,extra={})=>({id,username,full_name:username,role:'user',is_active:true,tenant_id:1,...extra});
  const paginate=items=>({items,total:items.length,page:1,page_size:100});
  const analytics={quality:{total:0,approved:0,rejected:0,pending:0,negative_samples:0,sources:{manual:0,model:0,unknown:0},review_origins:{human:0,automatic:0,unknown:0},process_pass_rate:null,categories:[]},workload:{human_annotation_images:2,human_review_images:3,human_annotation_events:4,human_review_events:5,automatic_annotation_events:0,automatic_review_events:0,rework_events:0,unknown_events:0},efficiency:{timing_status:'not_collected',active_seconds:null,annotation_seconds:null,review_seconds:null,timed_annotation_events:0,timed_review_events:0,annotation_per_hour:null,review_per_hour:null,users:[{user_id:2,username:'annotator',annotation_images:2,review_images:3,annotation_events:4,review_events:5,active_seconds:null,annotation_seconds:null,review_seconds:null,timed_annotation_events:0,timed_review_events:0,annotation_per_hour:null,review_per_hour:null}]},definitions:{quality:'fixture',workload:'fixture',timing:'fixture',efficiency:'fixture'}};
  await page.addInitScript(()=>{if(location.protocol==='http:')localStorage.setItem('token','isolated-permission-fixture');});
  const api=/^http:\/\/localhost:3000\/api\//;
  await page.route(api,async route=>{
    const req=route.request(),url=new URL(req.url()),path=url.pathname,method=req.method();
    reads.push(url.href);if(method!=='GET')writes.push({path,method,body:req.postDataJSON()});
    let data;
    if(path==='/api/auth/me')data=user(role==='admin'?1:2,'fixture',{role});
    else if(path==='/api/users')data=url.searchParams.get('page')==='2'?{items:[user(3,'second-page',{role:'annotator'})],total:4,page:2,page_size:100}:{items:[user(2,'annotator'),user(4,'disabled',{is_active:false}),user(1,'admin',{role:'admin'})],total:4,page:1,page_size:100};
    else if(path.endsWith('/assignments')){if(method==='PUT'){if(saveError)return route.fulfill({status:500,json:{detail:'隔离保存失败'}});assigned=req.postDataJSON().user_ids;}data={user_ids:assigned};}
    else if(path==='/api/projects')data=paginate(empty?[]:[project]);
    else if(path===`/api/projects/${project.id}`)data=project;
    else if(path==='/api/assets')data=paginate([{id:'asset-fixture',project_id:project.id,original_name:'crop.jpg',source_type:'model_crop'}]);
    else if(path.endsWith('/content'))return route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/>'});
    else if(path==='/api/annotations')data=paginate([]);
    else if(path.endsWith('/stats'))data=project;
    else if(path.endsWith('/matches'))data=[];
    else if(path.endsWith('/annotation-models'))data=[];
    else if(path.endsWith('/matches/smart-search/platforms'))data=[];
    else if(path==='/api/analytics')data=analytics;
    else {unexpected.push(method+' '+path);return route.fulfill({status:500,json:{detail:'未配置的隔离请求'}});}
    await route.fulfill({json:data});
  });
  try{
    await page.goto('http://localhost:3000/annotations');await page.getByRole('region',{name:'标注项目 '+project.project_name}).waitFor();
    ensure(!await page.getByRole('menuitem',{name:'模型仓库',exact:true}).count(),'ordinary menu excludes model repository');
    for(const path of ['/model-repository','/projects','/analytics','/training','/review-queue']){
      await page.goto('http://localhost:3000'+path);await page.waitForURL('**/annotations');
    }
    checks.push('ordinary direct admin routes redirect');
    await page.goto('http://localhost:3000/annotate/project/'+project.id);await page.getByRole('link',{name:'编辑图片描述',exact:true}).waitFor();
    ensure(!await page.getByRole('button',{name:/删除/}).count(),'ordinary vector workspace hides destructive controls');
    await page.getByText('文搜图标注',{exact:true}).click();await page.getByRole('button',{name:'检索并标注',exact:true}).waitFor();
    ensure(!await page.getByRole('button',{name:/删除|上传查询图片/}).count(),'ordinary vector search hides management controls');
    await page.goto('http://localhost:3000/annotations?project_id='+project.id);await page.getByText('检索匹配标注审核',{exact:true}).waitFor();
    ensure(!await page.getByRole('button',{name:'大模型审核待审匹配'}).count(),'ordinary review loads without AI management requests');
    empty=true;await page.goto('http://localhost:3000/annotations');await page.getByText('暂无分配给你的项目，请联系管理员分配项目').waitFor();checks.push('unassigned user sees clear empty state');await page.screenshot({path:'output/playwright/project-permissions-ordinary.png'});empty=false;
    role='admin';await page.goto('http://localhost:3000/projects');await page.getByRole('button',{name:'分配项目',exact:true}).click();
    const modal=page.getByRole('dialog',{name:'分配项目 · '+project.project_name});await modal.waitFor();
    const members=modal.getByRole('combobox',{name:'项目成员'});await members.click();
    await page.getByText('second-page（second-page）',{exact:true}).click();
    ensure(!await page.locator('.ant-select-dropdown:visible').getByText('disabled（disabled）',{exact:true}).count(),'inactive users excluded');
    ensure(!await page.locator('.ant-select-dropdown:visible').getByText('admin（admin）',{exact:true}).count(),'administrators excluded');
    await members.press('Escape');await page.screenshot({path:'output/playwright/project-permissions-assignment.png'});await modal.getByRole('button',{name:'保存分配',exact:true}).click();await modal.waitFor({state:'hidden'});
    ensure(JSON.stringify(assigned)==='[2,3]','assignment saves existing and second-page ordinary users');
    await page.getByRole('button',{name:'分配项目',exact:true}).click();await members.waitFor();
    await modal.locator('.ant-select-selection-item-remove').first().click();await modal.locator('.ant-select-selection-item-remove').first().click();
    saveError=true;await modal.getByRole('button',{name:'保存分配',exact:true}).click();await modal.getByText('项目分配保存失败，请重试').waitFor();ensure(await modal.isVisible(),'save failure keeps assignment editor');
    saveError=false;await modal.getByRole('button',{name:'保存分配',exact:true}).click();await modal.waitFor({state:'hidden'});ensure(assigned.length===0,'assignment supports revoking all members');
    await page.goto('http://localhost:3000/analytics?project_id='+project.id);await page.getByRole('columnheader',{name:'人工标注（张）',exact:true}).waitFor();await page.getByRole('columnheader',{name:'人工审核（张）',exact:true}).waitFor();
    const row=page.getByRole('row').filter({hasText:'annotator'});ensure((await row.getByRole('cell').allTextContents()).slice(1,5).join(',')==='2,3,4,5','user table distinguishes images and events');
    await page.getByLabel('统计开始日期').fill('2026-11-01');await page.getByLabel('统计结束日期').fill('2026-11-02');await Promise.all([page.waitForResponse(response=>response.url().includes('/api/analytics?')&&response.url().includes('start=')),page.getByRole('button',{name:'查询统计',exact:true}).click()]);
    const query=new URL(reads.filter(url=>url.includes('/api/analytics?')).at(-1));
    ensure(query.searchParams.get('project_id')===project.id&&Date.parse(query.searchParams.get('end'))-Date.parse(query.searchParams.get('start'))===2*86400000,'analytics retains project and inclusive end date');
    await page.getByRole('columnheader',{name:'人工标注（张）',exact:true}).scrollIntoViewIfNeeded();
    await page.screenshot({path:'output/playwright/project-permissions-analytics.png'});
    ensure(unexpected.length===0,'all API requests intercepted with fixtures');
    return {checks,writes,scope:'isolated browser mocks only; no business database access'};
  }finally{await page.goto('about:blank');await page.unroute(api);}
}
