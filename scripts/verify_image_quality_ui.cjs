async page=>{
  await page.unrouteAll({behavior:'ignoreErrors'});page.setDefaultTimeout(10000);
  const base='http://127.0.0.1:3000',projectId='11111111-1111-4111-8111-111111111111',modelId='22222222-2222-4222-8222-222222222222';
  const ids=Array.from({length:5002},(_,i)=>`aaaaaaaa-aaaa-4aaa-8aaa-${String(i+1).padStart(12,'0')}`);
  const names=['完整车辆.png','局部车.png','调用失败.png'];
  const models=[{id:modelId,name:'Qwen在线',kind:'caption',protocol:'openai_chat',enabled:true,revision:1,connection_check:{status:'online',checked_revision:1}},
    {id:'33333333-3333-4333-8333-333333333333',name:'Qwen离线',kind:'caption',protocol:'openai_chat',enabled:true,revision:1,connection_check:{status:'offline',checked_revision:1}},
    {id:'44444444-4444-4444-8444-444444444444',name:'FastVLM',kind:'caption',protocol:'model_service',enabled:true,revision:1},
    {id:'55555555-5555-4555-8555-555555555555',name:'JEV属性',kind:'caption',protocol:'jev_decide',enabled:true,revision:1},
    {id:'66666666-6666-4666-8666-666666666666',name:'停用Qwen',kind:'caption',protocol:'openai_chat',enabled:false,revision:1}];
  const project={id:projectId,project_name:'车辆质量检查',task_type:'caption',status:'active',label_schema:{annotation_scope:'crops'}};
  const writes=[],errors=[],assetQueries=[],contentReads=[],jobs=[];let total=3,completed=false,assetReads=0,projectReads=0;
  const check=(value,message)=>{if(!value)throw Error(message);};
  const headers={'X-Asset-Crop-Depth-Filter':'1','X-Asset-Crop-Batch-Filter':'1'};
  const summary=job=>({...job,counts:completed?{completed:2,failed:1}:job.counts,quality_counts:completed?{passed:1,deleted:1}:{passed:0,deleted:0}});
  await page.addInitScript(()=>localStorage.setItem('token','isolated-image-quality'));
  page.on('pageerror',error=>errors.push(error.message));
  await page.route(base+'/api/**',async route=>{
    const req=route.request(),u=new URL(req.url()),p=u.pathname;
    if(p==='/api/auth/me')return route.fulfill({json:{id:1,tenant_id:1,username:'管理员',role:'admin'}});
    if(p==='/api/analytics/activity')return route.fulfill({json:{}});
    if(p==='/api/projects/'+projectId){projectReads++;return route.fulfill({json:project});}
    if(p==='/api/model-services')return route.fulfill({json:models});
    if(p==='/api/assets/selection'){assetQueries.push(Object.fromEntries(u.searchParams));return route.fulfill({headers,json:{asset_ids:ids.slice(0,total),total}});}
    if(p==='/api/assets'){
      assetReads++;assetQueries.push(Object.fromEntries(u.searchParams));
      const selected=completed?ids.slice(0,total).filter(id=>id!==ids[1]):ids.slice(0,total);
      return route.fulfill({headers,json:{items:selected.slice(0,20).map((id,i)=>({id,project_id:projectId,original_name:names[ids.indexOf(id)]||'车辆-'+i+'.png',source_type:'model_crop',width:64,height:48,device_info:{crop:{depth:1}}})),total:selected.length,page:1,page_size:20}});
    }
    if(p.endsWith('/content')){contentReads.push(p);return route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="64" height="48"><rect width="64" height="48" fill="#314c74"/></svg>'});}
    if(p.endsWith('/work/jobs')&&req.method()==='POST'){
      const body=req.postDataJSON();writes.push({path:p,body});check(body.operation==='quality','Quality entry submitted another operation');
      const job={id:'job-'+jobs.length,operation:'quality',model_id:modelId,revision:1,model_name:'Qwen在线',total:body.asset_ids.length,counts:{pending:body.asset_ids.length},quality_counts:{passed:0,deleted:0},quality_requirement:body.prompt};
      jobs.unshift(job);return route.fulfill({status:201,json:job});
    }
    if(p.endsWith('/work/jobs'))return route.fulfill({json:jobs.map(summary)});
    if(/\/work\/jobs\/job-\d+\/(retry|cancel)$/.test(p)){
      writes.push({path:p,body:null});return route.fulfill({json:{changed_count:1}});
    }
    if(/\/work\/jobs\/job-\d+$/.test(p)){
      const job=jobs.find(job=>p.endsWith(job.id));
      const items=[{id:'item-good',asset_id:ids[0],original_name:names[0],status:completed?'completed':'pending',attempt:1,error:null,result:completed?{passed:true,reason:'主体整车完整且清晰',deleted:false}:null},
        {id:'item-bad',asset_id:completed?null:ids[1],original_name:names[1],status:completed?'completed':'pending',attempt:1,error:null,result:completed?{passed:false,reason:'车身被画面边界截断',deleted:true,original_name:names[1]}:null},
        {id:'item-failure',asset_id:ids[2],original_name:names[2],status:completed?'failed':'pending',attempt:1,error:completed?'模型服务超时':null,result:null}];
      return route.fulfill({json:{...summary(job),page:1,items}});
    }
    if(req.method()!=='GET')throw Error('Unexpected write '+p);
    return route.fulfill({json:[]});
  });
  const chooseModel=async()=>{await page.getByRole('combobox',{name:'处理模型',exact:true}).click();await page.locator('.ant-select-dropdown:visible .ant-select-item-option-content').getByText('Qwen在线 · r1',{exact:true}).click();};
  const requirement=()=>page.getByRole('textbox',{name:'图片质量要求',exact:true});
  const edit=()=>page.getByRole('button',{name:'编辑质检提示词',exact:true});
  const start=()=>page.getByRole('button',{name:/开始质检并删除不合格图片 · 已选/});
  await page.setViewportSize({width:1480,height:1080});await page.goto(base+'/projects/'+projectId+'/prepare?operation=quality');
  await edit().waitFor();check(writes.length===0,'Opening quality entry started a job');
  await edit().click();check((await requirement().inputValue()).includes('车辆'),'Vehicle example missing');await page.getByRole('button',{name:'取消',exact:true}).click();
  await page.getByRole('combobox',{name:'处理模型',exact:true}).click();
  const options=page.locator('.ant-select-dropdown:visible');check(!await options.getByText('FastVLM · r1',{exact:true}).count()&&!await options.getByText('JEV属性 · r1',{exact:true}).count()&&!await options.getByText('停用Qwen · r1',{exact:true}).count(),'Unsupported model offered');
  check(await options.locator('.ant-select-item-option').filter({hasText:'Qwen离线'}).getAttribute('aria-disabled')==='true','Offline model enabled');
  await options.locator('.ant-select-item-option-content').getByText('Qwen在线 · r1',{exact:true}).click();
  await page.getByRole('button',{name:'选择当前筛选全部图片',exact:true}).click();
  await page.getByRole('combobox',{name:'处理方式',exact:true}).click();await page.locator('.ant-select-dropdown:visible .ant-select-item-option-content').getByText('生成描述',{exact:true}).click();
  check(!new URL(page.url()).searchParams.has('operation'),'Leaving quality kept stale URL operation');
  await page.getByRole('link',{name:'图片质量检查',exact:true}).click();await edit().waitFor();
  check(await start().isDisabled()&&(await start().innerText()).includes('已选 0 张')&&writes.length===0,'Same-page quality entry reused previous model/selection or submitted automatically');
  await chooseModel();await page.getByRole('button',{name:'选择当前筛选全部图片',exact:true}).click();
  await page.screenshot({path:'output/playwright/image-quality-setup.png',fullPage:true,animations:'disabled'});
  await edit().click();await requirement().fill('   ');check(await page.getByRole('button',{name:'应用提示词',exact:true}).isDisabled(),'Blank quality requirements allowed deletion');
  const rules='只保留一辆完整且清晰可辨的主体车辆，不接受严重遮挡和车身截断。';await requirement().fill(rules);await page.getByRole('button',{name:'应用提示词',exact:true}).click();await start().click();
  check(writes[0].body.prompt===rules&&writes[0].body.model_id===modelId&&writes[0].body.asset_ids.join()===ids.slice(0,3).join(),'Quality payload wrong');
  check(assetQueries.every(query=>query.project_id===projectId&&query.scope==='annotation'),'Selection scope escaped project annotation images');
  await page.getByText('图片质量检查',{exact:true}).first().waitFor();const beforeAssets=assetReads,beforeProjects=projectReads;completed=true;
  await page.getByText('合格 1 张 · 已删除 1 张',{exact:true}).waitFor();
  await page.getByRole('button',{name:'查看明细',exact:true}).first().click();
  const badRow=page.getByRole('row').filter({hasText:'车身被画面边界截断'});await badRow.waitFor();
  check(await badRow.getByText(names[1],{exact:true}).isVisible()&&!await badRow.getByRole('link').count(),'Deleted image lost name or kept dead annotation link');
  check(await badRow.getByText('不合格 · 已删除',{exact:true}).isVisible(),'Deleted outcome missing');
  await page.getByRole('row').filter({hasText:'模型服务超时'}).getByText('失败 · 图片保留',{exact:true}).waitFor();
  check(assetReads>beforeAssets&&projectReads>beforeProjects,'Quality completion did not refresh project data');
  const afterRefresh=assetReads;await page.waitForTimeout(3400);check(assetReads===afterRefresh,'Completed quality refreshes asset list every poll');
  await page.screenshot({path:'output/playwright/image-quality-results.png',fullPage:true,animations:'disabled'});
  await page.getByRole('button',{name:'重试失败与取消项',exact:true}).click();check(writes.at(-1).path.endsWith('/retry'),'Quality retry missing');
  await page.getByRole('link',{name:'图片质量检查',exact:true}).click();await edit().waitFor();
  check(await start().isDisabled()&&(await start().innerText()).includes('已选 0 张'),'Repeated quality entry did not leave queue/reset form');
  completed=false;total=5002;await page.reload();await edit().waitFor();await chooseModel();
  await page.getByRole('button',{name:'选择当前筛选全部图片',exact:true}).click();await start().click();
  const batches=writes.filter(write=>write.body?.operation==='quality').slice(1);check(batches.length===2&&batches[0].body.asset_ids.length===5000&&batches[1].body.asset_ids.length===2,'Large quality selection was not batched safely');
  check(new Set(batches.flatMap(batch=>batch.body.asset_ids)).size===5002,'Large quality selection duplicated/lost images');
  check(errors.length===0,errors.join('\n'));
  return {checks:12,qualityBatches:batches.map(batch=>batch.body.asset_ids.length),automaticJobsOnOpen:0,samePageEntryResetsSelection:true,failedImagePreserved:true,completedRefreshOnce:true,manualReviewQueue:false,errors};
}
