async page=>{
  page.setDefaultTimeout(12000);
  const origin='http://localhost:3000',id='11111111-1111-4111-8111-111111111111';
  const checks=[],requests=[],writes=[],unexpected=[],errors=[],snapshotReads={detection:0,caption:0};
  let taskType='detection',saved;
  const project=()=>({id,project_name:'隔离训练项目',task_type:taskType,status:'active',label_schema:{labels:['car']},total_assets:2,annotated_count:2,reviewed_count:2,created_at:'2026-11-06T00:00:00Z'});
  const snapshot=()=>({id:taskType==='caption'?'22222222-2222-4222-8222-222222222222':'33333333-3333-4333-8333-333333333333',
    format:taskType==='caption'?'swift_sft':'yolo',content_hash:'a'.repeat(64),created_at:'2026-11-06T00:00:00Z',
    manifest:taskType==='caption'?{training:{format:'swift_sft',source:'descriptions',samples:2}}:
      {labels:['car'],approved_only:true,assets:[{split:'train',review_status:'approved'},{split:'val',review_status:'approved'}]}});
  const pageData=items=>({items,total:items.length,page:1,page_size:20});
  const addCheck=(ok,label)=>{if(!ok)throw Error(label);checks.push(label);};
  const user={id:1,username:'fixture-admin',role:'admin',tenant_id:1,is_active:true};
  const api=/^https?:\/\/[^/]+\/api\//;
  const handler=async route=>{
    const req=route.request(),url=new URL(req.url()),path=url.pathname,method=req.method();
    let body;try{body=req.postDataJSON();}catch{}
    requests.push({path,method,search:url.search});
    if(method!=='GET')writes.push({path,method,body});
    let data,status=200;
    if(path==='/api/auth/me')data=user;
    else if(path===`/api/projects/${id}`)data=project();
    else if(path==='/api/projects')data=pageData([project()]);
    else if(path===`/api/projects/${id}/stats`)data={total_assets:2,annotated_count:2,reviewed_count:2};
    else if(path==='/api/training/catalog')data={
      yolo26:{name:'YOLO26',image:'fixture/yolo:isolated',defaults:{epochs:2,batch:4,device:'auto'},environment:{CUDA_VISIBLE_DEVICES:'auto'},schema:{properties:{epochs:{type:'integer'},batch:{type:'integer'},device:{type:'string'}}}},
      swift_sft:{name:'Swift 大模型SFT',image:'fixture/swift:isolated',defaults:{task_type:'causal_lm',loss_type:null,num_train_epochs:1},environment:{CUDA_VISIBLE_DEVICES:'auto'},schema:{properties:{task_type:{type:'string',enum:['causal_lm','embedding']},num_train_epochs:{type:'number'}}}}};
    else if(path==='/api/training/servers')data=[{id:'default',name:'隔离GPU服务器',enabled:true,revision:1,source:'ssh_manager'}];
    else if(path==='/api/training/defaults')data={model_path:'/fixture/models/base.pt',output_root:'/fixture/runs'};
    else if(path==='/api/gpu-resources')data={server_name:'fixture',collected_at:'2026-11-06T00:00:00Z',cached:true,processes_complete:true,warnings:[],
      gpus:[{uuid:'GPU-fixture-1',index:0,name:'隔离GPU',memory_free_mib:24576}],processes:[]};
    else if(path===`/api/export/projects/${id}/snapshots`){
      snapshotReads[taskType]++;
      if(snapshotReads[taskType]===1){status=503;data={detail:'隔离冻结列表暂时不可用，请重试'};}
      else data=[snapshot()];
    }else if(path==='/api/training/runs'&&method==='POST'){
      saved={id:'44444444-4444-4444-8444-444444444444',name:body.name,kind:body.kind,status:'draft',config:body,
        source:{kind:'snapshot',id:body.snapshot_id,project_id:id,sha256:'a'.repeat(64),format:snapshot().format},
        server:{id:'default',name:'隔离GPU服务器'},inspection:{},output_dir:'/fixture/runs/saved',created_at:'2026-11-06T00:00:00Z'};
      data=saved;status=201;
    }else if(path==='/api/training/runs')data=pageData(saved?[saved]:[]);
    else if(path.startsWith('/api/')){
      unexpected.push(`${method} ${path}${url.search}`);
      return route.fulfill({status:599,json:{detail:'未配置的隔离接口'}});
    }
    return route.fulfill({status,json:data});
  };
  const onError=error=>errors.push(error.message);
  page.on('pageerror',onError);
  await page.addInitScript(user=>{localStorage.setItem('token','isolated-project-training');localStorage.setItem('user',JSON.stringify(user));},user);
  await page.route(api,handler);
  try{
    await page.setViewportSize({width:1440,height:1000});
    for(const type of ['detection','caption']){
      taskType=type;saved=undefined;
      await page.goto(origin+`/projects/${id}/deliver`);
      await page.getByRole('button',{name:'创建训练任务',exact:true}).waitFor();
      addCheck(await page.getByRole('dialog').count()===0,`${type}：进入项目先显示任务列表，不自动创建`);
      const query=requests.filter(row=>row.path==='/api/training/runs'&&row.method==='GET').at(-1);
      addCheck(new URLSearchParams(query?.search).get('project_id')===id,`${type}：任务列表按当前项目过滤`);
      await page.getByRole('button',{name:'创建训练任务',exact:true}).click();
      const dialog=page.getByRole('dialog',{name:'创建训练任务',exact:true});await dialog.waitFor();
      const projectPicker=dialog.getByRole('combobox',{name:type==='caption'?'平台标注项目':'冻结导出所属项目'});
      addCheck(await projectPicker.isDisabled(),`${type}：创建表单锁定当前项目`);
      addCheck(await dialog.getByText('使用平台冻结数据训练',{exact:true}).count()===1,`${type}：创建只支持平台冻结数据`);
      addCheck(await dialog.getByText(/服务器已有路径|服务器训练JSONL绝对路径|服务器已有YAML配置绝对路径/).count()===0,`${type}：没有服务器数据来源入口`);
      const failure=type==='caption'?'读取冻结版本失败，请点击“重新读取冻结版本”重试':'隔离冻结列表暂时不可用，请重试';
      await dialog.getByText(failure,{exact:true}).first().waitFor();
      addCheck(snapshotReads[type]===1,`${type}：首次冻结版本请求503后显示恢复提示`);
      if(type==='caption')addCheck(await dialog.getByRole('button',{name:'冻结当前标注',exact:true}).isEnabled(),'caption：读取旧版本失败仍可冻结当前标注');
      await dialog.getByRole('button',{name:'重新读取冻结版本'}).click();
      const snapshotPicker=dialog.getByRole('combobox',{name:type==='caption'?'平台数据冻结版本':'冻结版本'});
      await snapshotPicker.click();
      await page.locator('.ant-select-dropdown:visible').getByText(type==='caption'?/swift_sft · 图片描述配对 · 2 条/:/YOLO ·/).click();
      addCheck(snapshotReads[type]===2,`${type}：锁定项目重试成功并可选择冻结版本`);
      await dialog.getByRole('textbox',{name:'任务名称'}).fill(`${type}隔离冻结训练`);
      const gpu=dialog.getByRole('combobox',{name:'物理GPU（明确分配）'});
      await gpu.click();await page.locator('.ant-select-dropdown:visible').getByText(/隔离GPU · 空闲24GiB/).click();await page.keyboard.press('Escape');
      await dialog.getByRole('textbox',{name:'服务器基础权重文件或模型目录'}).waitFor();
      await page.screenshot({path:`output/playwright/project-training-${type}-retry.png`,fullPage:true,animations:'disabled'});
      const before=writes.length;
      await dialog.getByRole('button',{name:'保存任务配置',exact:true}).click();
      await dialog.waitFor({state:'hidden'});
      const write=writes.slice(before).find(row=>row.path==='/api/training/runs'&&row.method==='POST');
      addCheck(!!write&&write.body.snapshot_id===snapshot().id&&write.body.dataset_path==='',`${type}：保存只提交所选平台冻结版本`);
      addCheck(write.body.kind===(type==='caption'?'swift_sft':'yolo26'),`${type}：默认选择适用训练器`);
      addCheck(saved?.status==='draft',`${type}：保存为待启动任务`);
      await page.screenshot({path:`output/playwright/project-training-${type}-saved.png`,fullPage:true,animations:'disabled'});
    }
    addCheck(writes.length===2&&writes.every(row=>row.method==='POST'&&row.path==='/api/training/runs'),'只保存两个隔离草稿，没有冻结、预检查或启动训练请求');
    addCheck(unexpected.length===0,`全部API请求均被已知夹具接管：${unexpected.join(', ')}`);
    addCheck(errors.length===0,`浏览器无运行时错误：${errors.join(', ')}`);
    return {checks,requests:requests.length,writes,snapshotReads,scope:'All HTTP(S) /api/ requests intercepted; only mock training drafts saved; no real training or business writes'};
  }catch(error){await page.screenshot({path:'output/playwright/project-training-failure.png',fullPage:true});throw Error(error.message+'\nBODY: '+(await page.locator('body').innerText()).slice(-10000)+'\nREQUESTS: '+JSON.stringify(requests));}finally{await page.goto('about:blank');await page.unroute(api,handler);page.off('pageerror',onError);}
}
