async page => {
  await page.bringToFront();await page.emulateMedia({reducedMotion:'reduce'});await page.unrouteAll();page.setDefaultTimeout(10000);
  const kind='dinov3_vits16_ltdetr',projectId='22222222-2222-4222-8222-222222222222';
  const snapshotId='33333333-3333-4333-8333-333333333333',runId='44444444-4444-4444-8444-444444444444';
  const writes=[],checks=[],errors=[];let run,failSave=true;
  page.on('pageerror',error=>errors.push(error.message));
  await page.route(/^http:\/\/localhost:3000\/api\//,async route=>{
    const request=route.request(),url=new URL(request.url()),path=url.pathname,method=request.method();
    const json=(value,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(value)});
    if(method!=='GET')writes.push({path,method,input:request.postDataJSON()});
    if(path==='/api/auth/me')return json({id:1,username:'isolated',role:'admin',tenant_id:1});
    if(path==='/api/training/catalog')return route.fulfill({contentType:'application/json',path:'output/dinov3-catalog.json'});
    if(path==='/api/training/servers')return json([{id:'default',name:'隔离服务器',enabled:true}]);
    if(path==='/api/training/defaults')return json({model_path:url.searchParams.get('kind')===kind?'/models/dinov3_vits16_ltdetr_coco.pt':'/models/yolo.pt',output_root:'/data/runs'});
    if(path==='/api/gpu-resources')return json({server_name:'isolated',warnings:[],gpus:[{uuid:'GPU-test',index:0,name:'Test GPU',memory_free_mib:20000}],processes:[]});
    if(path==='/api/projects')return json({items:[{id:projectId,project_name:'电瓶车项目',task_type:'detection'}],total:1});
    if(path===`/api/export/projects/${projectId}/snapshots`)return json([
      {id:snapshotId,format:'coco',created_at:'2026-10-19T00:00:00Z',manifest:{labels:['bicycle','电瓶车']}},
      {id:'55555555-5555-4555-8555-555555555555',format:'yolo',created_at:'2026-10-19T00:00:00Z',manifest:{labels:['电瓶车']}}
    ]);
    if(path==='/api/training/runs'){
      if(method==='GET')return json({items:run?[run]:[],total:run?1:0});
      if(failSave){failSave=false;return json({detail:'独立验证：请完成train/val框标注'},400);}
      const input=request.postDataJSON();run={id:runId,name:input.name,kind,status:'draft',config:input,
        server:{id:'default',name:'隔离服务器'},source:{kind:'snapshot',id:snapshotId,project_id:projectId,labels:['bicycle','电瓶车']},
        inspection:{},output_dir:'/data/runs/'+runId,created_at:'2026-10-19T00:00:00Z'};
      return json(run,201);
    }
    if(path===`/api/training/runs/${runId}`){
      if(method==='PUT'){const input=request.postDataJSON();run={...run,name:input.name,config:input};}
      return json(run);
    }
    if(path===`/api/training/runs/${runId}/artifacts`)return json({files:[{path:'exported_models/exported_best.pt',bytes:1000,role:'weights'}],truncated:false});
    if(path==='/api/model-services')return json([]);
    if(path==='/api/model-previews/config')return json({max_images:5000,max_upload_size:1000000,max_expanded_size:10000000,max_image_size:1000000});
    if(path==='/api/model-previews')return json({items:[],total:0});
    return json({detail:'isolated unhandled '+path},404);
  });
  await page.goto('http://localhost:3000/login');await page.evaluate(()=>{
    localStorage.setItem('token','isolated-dinov3');localStorage.setItem('user',JSON.stringify({role:'admin',username:'隔离验收'}));
  });
  await page.setViewportSize({width:1440,height:1100});await page.goto('http://localhost:3000/training');
  await page.getByRole('button',{name:'创建训练任务',exact:true}).click();
  const dialog=page.getByRole('dialog',{name:/^(创建训练任务|编辑训练任务配置)$/});
  const select=async(label,text)=>{
    await page.getByRole('combobox',{name:label}).click();
    await page.locator('.ant-select-dropdown:visible').getByText(text,{exact:true}).click();
  };
  await select('训练器','DINOv3 ViT-S/16 · LTDETR 检测微调');
  await dialog.getByText('DINOv3 电瓶车等目标的监督检测微调',{exact:true}).waitFor();
  if(await dialog.getByLabel('训练镜像').inputValue()!=='lightly/train:latest')throw Error('Wrong Lightly image');
  await dialog.getByLabel('任务名称').fill('电瓶车DINOv3测试');
  await select('物理GPU（明确分配）','0 · Test GPU · 空闲20GiB');
  await page.keyboard.press('Escape');
  await select('训练数据来源','平台冻结导出集（检测标注）');
  await select('冻结导出所属项目','电瓶车项目');
  await page.getByRole('combobox',{name:'冻结版本'}).click();
  const options=page.locator('.ant-select-dropdown:visible');
  await options.getByText(/COCO ·/).click();
  await page.getByRole('combobox',{name:'训练标签'}).click();
  await page.locator('.ant-select-dropdown:visible .ant-select-item-option-content').filter({hasText:'bicycle'}).click();
  await page.keyboard.press('Escape');
  const failedSave=page.waitForResponse(response=>response.url().endsWith('/api/training/runs')&&response.request().method()==='POST'&&response.status()===400);
  await dialog.getByRole('button',{name:'保存任务配置'}).click();
  await failedSave;
  if(!await dialog.isVisible())throw Error('Failed save closed training form');
  await dialog.getByRole('button',{name:'保存任务配置'}).click();
  await dialog.waitFor({state:'hidden'});
  const submitted=writes.filter(item=>item.method==='POST'&&item.path==='/api/training/runs').at(-1).input;
  if(submitted.kind!==kind||submitted.snapshot_id!==snapshotId||submitted.parameters.steps!==1000||submitted.parameters.batch_size!==4||submitted.model_path!=='/models/dinov3_vits16_ltdetr_coco.pt'||submitted.selected_labels.join()!=='电瓶车')throw Error('Wrong submitted DINOv3 contract');
  checks.push('DINOv3 catalog, latest image, GPU, COCO frozen labels, local full weights, typed defaults, API error retains form');
  await page.getByRole('button',{name:'编辑配置',exact:true}).click();
  await page.getByLabel('总训练步数',{exact:true}).fill('500');
  await page.getByLabel('冻结DINOv3骨干',{exact:true}).click();
  await page.setViewportSize({width:390,height:844});
  await page.waitForFunction(()=>document.documentElement.scrollWidth<=innerWidth);
  await page.screenshot({path:'output/playwright/dinov3-training-mobile.png'});
  await dialog.getByRole('button',{name:'保存任务配置'}).click();await dialog.waitFor({state:'hidden'});
  const edited=writes.find(item=>item.method==='PUT').input;
  if(edited.parameters.steps!==500||!edited.parameters.backbone_freeze)throw Error('DINOv3 edit lost parameters');
  checks.push('Draft edit preserves source; total steps/freeze persisted; 390px layout');
  run.status='completed';await page.setViewportSize({width:1440,height:1100});
  await page.goto('http://localhost:3000/model-preview');
  await select('预览模型来源','YOLO / RF-DETR / DINOv3 训练权重');
  await page.getByRole('combobox',{name:'权重训练任务',exact:true}).click();
  await page.locator('.ant-select-dropdown:visible').getByText(/电瓶车DINOv3测试/).click();
  await page.getByRole('combobox',{name:'训练权重文件',exact:true}).click();
  await page.locator('.ant-select-dropdown:visible .ant-select-item-option-content').filter({hasText:/exported_models\/exported_best.pt/}).click();
  await page.screenshot({path:'output/playwright/dinov3-checkpoint-preview.png'});
  checks.push('DINOv3 completed training and full exported detector selectable for image preview');
  if(writes.some(item=>/\/(start|precheck|stop)$/.test(item.path)||item.path==='/api/model-previews'))throw Error('Unexpected compute operation');
  if(errors.length)throw Error(errors.join('\n'));
  const result={checks,writes:writes.map(({path,method})=>({path,method})),errors};
  await page.goto('about:blank');await page.unrouteAll();
  return result;
}
