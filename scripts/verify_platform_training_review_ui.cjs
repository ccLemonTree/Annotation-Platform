async page=>{
  await page.unrouteAll();page.setDefaultTimeout(10000);
  const errors=[],writes=[],checks=[],projectId='33333333-3333-4333-8333-333333333333',queryId='44444444-4444-4444-8444-444444444444';
  const project={id:projectId,project_name:'已完成匹配标注',task_type:'vector',label_schema:{annotation_scope:'crops'},total_assets:6,annotated_count:0,reviewed_count:0};
  const query={id:queryId,query:{text:'穿黑色衣服的人'},space:{revision:1},version:1,hits:['correct','incorrect','unknown'].map((judgment,i)=>({asset_id:'55555555-5555-4555-8555-'+String(i).padStart(12,'0'),original_name:'crop-'+i+'.png',source_type:'model_crop',judgment,score:.3}))};
  const template=(name,task_type)=>({name,image:'existing/image:tag',schema:{properties:{task_type:{enum:[task_type]},loss_type:{enum:['infonce','contrastive','online_contrastive','cosine_similarity']}}},defaults:{task_type,loss_type:task_type==='embedding'?'infonce':null},environment:{}});
  const catalog={yolo26:{name:'YOLO26',image:'existing/image:tag',schema:{properties:{}},defaults:{},environment:{}},wemm:template('WeMM 向量微调','embedding'),swift_sft:template('Swift 大模型SFT','causal_lm')};
  let freezeFails=true,reviewFails=true,frozen=[],run;
  page.on('pageerror',error=>errors.push(error.message));
  await page.route(/^http:\/\/localhost:3000\/api\//,route=>{
    const req=route.request(),path=new URL(req.url()).pathname,method=req.method(),json=(value,status=200)=>route.fulfill({json:value,status});
    if(method!=='GET')writes.push({path,method,input:req.postDataJSON()});
    if(path==='/api/auth/me')return json({role:'admin',username:'隔离验收'});
    if(path==='/api/projects')return json({items:[project],total:1});
    if(path==='/api/projects/'+projectId)return json(project);
    if(path.endsWith('/stats'))return json({total_assets:6,annotated_count:0,reviewed_count:0});
    if(path==='/api/annotations'||path==='/api/assets')return json({items:[],total:0});
    if(path.endsWith('/model-services')||path.endsWith('/work/jobs'))return json([]);
    if(path.endsWith('/matches'))return json([query]);
    if(path.endsWith('/review')){
      if(reviewFails){reviewFails=false;return json({detail:'判定已修改，请刷新重试'},409);}
      const data=req.postDataJSON();if(data.base_version!==query.version)throw Error('Stale review version');
      query.hits.find(h=>h.asset_id===data.asset_id).review_status=data.approved?'approved':'rejected';query.version++;return json(query);
    }
    if(path.includes('/assets/')&&path.endsWith('/content'))return route.fulfill({contentType:'image/png',body:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aC+sAAAAASUVORK5CYII=','base64')});
    if(path==='/api/training/catalog')return json(catalog);
    if(path==='/api/training/servers')return json([{id:'default',name:'验收服务器',enabled:true}]);
    if(path==='/api/training/defaults')return json({model_path:'/data/base-model',output_root:'/data/runs'});
    if(path==='/api/gpu-resources')return json({gpus:[{uuid:'GPU-test-1',index:0,name:'测试GPU',memory_free_mib:24000}]});
    if(path.endsWith('/snapshots'))return json(frozen);
    if(path.endsWith('/annotation-snapshots')){
      if(freezeFails){freezeFails=false;return json({detail:'同一查询对同一图片存在正确/错误冲突'},400);}
      const data=req.postDataJSON(),snapshot={id:'66666666-6666-4666-8666-'+String(frozen.length).padStart(12,'0'),format:data.dataset_format,created_at:new Date().toISOString(),manifest:{training:{format:data.dataset_format,source:data.sample_source,samples:2}}};
      frozen.unshift(snapshot);return json(snapshot,201);
    }
    if(path==='/api/training/runs'){
      if(method==='GET')return json({items:run?[run]:[],total:run?1:0});
      const input=req.postDataJSON();run={id:'77777777-7777-4777-8777-777777777777',name:input.name,kind:input.kind,status:'draft',server:{id:'default',name:'验收服务器'},config:input,source:{kind:'snapshot',id:input.snapshot_id,project_id:projectId},inspection:{},created_at:new Date().toISOString(),output_dir:'/data/runs/owned'};return json(run,201);
    }
    if(path==='/api/training/runs/'+run?.id){if(method==='PUT'){run={...run,name:req.postDataJSON().name,config:req.postDataJSON()};}return json(run);}
    return json({detail:'Unexpected fixture '+path},404);
  });
  await page.goto('http://localhost:3000/login');await page.evaluate(()=>{localStorage.setItem('token','isolated-test');localStorage.setItem('user',JSON.stringify({role:'admin',username:'隔离验收'}));});
  await page.setViewportSize({width:1440,height:1100});await page.goto('http://localhost:3000/annotations?project_id='+projectId);
  const reviews=page.locator('.ant-card').filter({has:page.getByText('检索匹配标注审核',{exact:true})}).last();
  await reviews.getByText('本页查询 1 条 · 已判定关系 2 条',{exact:true}).waitFor();
  await page.getByText('暂无图片描述或分类标注；检索匹配标注见上方审核区',{exact:true}).waitFor();
  const row=reviews.getByRole('row').filter({hasText:'crop-0.png'});
  await row.getByRole('button',{name:'通过匹配审核',exact:true}).click();await reviews.getByText('判定已修改，请刷新重试',{exact:true}).waitFor();
  await reviews.getByRole('button',{name:'刷新匹配判定',exact:true}).click();await row.getByRole('button',{name:'通过匹配审核',exact:true}).click();
  await row.getByText('已通过',{exact:true}).waitFor();
  await reviews.getByRole('row').filter({hasText:'crop-1.png'}).getByRole('button',{name:'驳回匹配审核',exact:true}).click();
  await reviews.getByRole('row').filter({hasText:'crop-1.png'}).getByText('已驳回',{exact:true}).waitFor();
  await page.setViewportSize({width:390,height:844});await reviews.scrollIntoViewIfNeeded();
  if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth))throw Error('Review mobile overflow');
  await page.screenshot({path:'output/playwright/matching-review-mobile.png'});checks.push('Saved judgments visible without image annotations; approval/rejection, conflict retry and 390px');
  const select=async(locator,text)=>{await locator.click();await page.locator('.ant-select-dropdown:visible').getByText(text,{exact:true}).click();};
  await page.setViewportSize({width:1440,height:1100});await page.goto('http://localhost:3000/training');await page.getByRole('button',{name:'创建训练任务',exact:true}).click();
  let dialog=page.getByRole('dialog',{name:'创建训练任务',exact:true});
  await select(dialog.locator('#kind'),'WeMM 向量微调');await dialog.locator('#name').fill('平台向量训练');
  await select(dialog.locator('#gpu_uuids'),'0 · 测试GPU · 空闲23GiB');
  await select(dialog.getByRole('combobox',{name:'训练数据来源',exact:true}),'平台标注数据（冻结版本）');
  await select(dialog.getByRole('combobox',{name:'平台标注项目',exact:true}),project.project_name);
  await dialog.getByRole('button',{name:'冻结当前标注',exact:true}).click();await dialog.getByText('同一查询对同一图片存在正确/错误冲突',{exact:true}).waitFor();
  await dialog.getByRole('button',{name:'冻结当前标注',exact:true}).click();await dialog.getByText('已选择固定版本：2 条训练样本。保存任务不会启动训练。',{exact:true}).waitFor();
  await page.setViewportSize({width:390,height:844});
  await page.waitForFunction(()=>document.documentElement.scrollWidth<=innerWidth);
  await dialog.getByRole('button',{name:'冻结当前标注',exact:true}).scrollIntoViewIfNeeded();await page.screenshot({path:'output/playwright/platform-training-mobile.png'});
  await dialog.getByRole('button',{name:'保存任务配置',exact:true}).click();await dialog.waitFor({state:'hidden'});
  const created=writes.find(r=>r.path==='/api/training/runs'&&r.method==='POST').input;
  if(!created.snapshot_id||created.dataset_path||created.selected_labels||created.project_id)throw Error('Invalid snapshot training contract');
  if(writes.find(r=>r.path.endsWith('/annotation-snapshots')).input.sample_source!=='matches')throw Error('Missing matches source');
  checks.push('WeMM platform option, matches freeze failure/retry, selected immutable version and draft save');
  await page.setViewportSize({width:1440,height:1100});await page.getByRole('button',{name:'编辑配置',exact:true}).click();
  dialog=page.getByRole('dialog',{name:'编辑训练任务配置',exact:true});await dialog.getByText('已选择固定版本：2 条训练样本。保存任务不会启动训练。',{exact:true}).waitFor();
  await dialog.locator('#name').fill('修改平台向量训练');await dialog.getByRole('button',{name:'保存任务配置',exact:true}).click();await dialog.waitFor({state:'hidden'});
  checks.push('Editing restores project and existing frozen version without server dataset path');
  await page.getByRole('button',{name:'Close',exact:true}).click().catch(()=>{});
  await page.reload();await page.getByRole('button',{name:'创建训练任务',exact:true}).click();dialog=page.getByRole('dialog',{name:'创建训练任务',exact:true});
  await select(dialog.locator('#kind'),'Swift 大模型SFT');await select(dialog.getByRole('combobox',{name:'训练数据来源',exact:true}),'平台标注数据（冻结版本）');
  await select(dialog.getByRole('combobox',{name:'平台标注项目',exact:true}),project.project_name);
  await dialog.getByRole('textbox',{name:'平台SFT训练指令',exact:true}).fill('描述<image>中的衣服');
  await dialog.getByRole('button',{name:'冻结当前标注',exact:true}).click();await dialog.getByText('已选择固定版本：2 条训练样本。保存任务不会启动训练。',{exact:true}).waitFor();
  const sft=writes.filter(r=>r.path.endsWith('/annotation-snapshots')).at(-1).input;
  if(sft.dataset_format!=='swift_sft'||sft.sample_source!=='descriptions'||sft.instruction!=='描述<image>中的衣服')throw Error('Wrong SFT source');
  checks.push('Swift SFT uses description source and editable instruction');
  if(writes.some(r=>/\/(start|stop|precheck)$/.test(r.path)))throw Error('Unexpected GPU action');
  if(errors.length)throw Error(errors.join('\n'));return {checks,pageErrors:errors,scope:'All APIs mocked; no business or GPU mutation'};
}
