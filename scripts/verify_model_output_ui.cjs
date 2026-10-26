async page=>{
  await page.unrouteAll();page.setDefaultTimeout(12000);
  const origin='http://127.0.0.1:3000',writes=[],errors=[];
  const source=await(await page.request.get(origin+'/src/pages/TrainingPage.tsx')).text();
  const react=source.match(/["'](\/node_modules\/\.vite\/deps\/react\.js\?[^"']+)["']/)[1],router=source.match(/["'](\/node_modules\/\.vite\/deps\/react-router-dom\.js\?[^"']+)["']/)[1];
  const id='11111111-1111-4111-8111-111111111111',wemmId='22222222-2222-4222-8222-222222222222',targetId='55555555-5555-4555-8555-555555555555';
  const config={kind:'rfdetr_2xlarge',gpu_uuids:['GPU-test-1'],model_path:'/data/base.pth',dataset_path:'/data/images',output_subdir:'train',parameters:{},image:'training-image:1'};
  const run={id,name:'RFDETR导出测试',kind:'rfdetr_2xlarge',status:'completed',server:{id:'default',name:'测试GPU服务器'},source:{kind:'remote'},config,inspection:{},output_dir:'/data/runs/'+id,created_at:new Date().toISOString()};
  const wemm={...run,id:wemmId,name:'WeMM合并测试',kind:'wemm',config:{...config,kind:'wemm',model_path:'/data/WeMM',parameters:{tuner_type:'lora'}}};
  const yolo={...run,id:'44444444-4444-4444-8444-444444444444',name:'YOLO导出测试',kind:'yolo26',config:{...config,kind:'yolo26'}};
  let fail=true;const jobs=[];
  await page.route('**/src/App.tsx*',route=>route.fulfill({contentType:'application/javascript',body:`import React from '${react}';import{BrowserRouter}from'${router}';import Provider from '/src/ui/PlatformProvider.tsx';import Training from '/src/pages/TrainingPage.tsx';localStorage.setItem('user',JSON.stringify({role:'admin'}));export default function App(){return React.createElement(BrowserRouter,null,React.createElement(Provider,null,React.createElement(Training)));}`}));
  await page.route('**/api/**',async route=>{
    const req=route.request(),path=new URL(req.url()).pathname;
    if(!path.startsWith('/api/'))return route.continue();
    const reply=json=>route.fulfill({json});
    if(path==='/api/training/catalog')return reply({yolo26:{name:'YOLO',defaults:{},schema:{properties:{}},environment:{}},rfdetr_2xlarge:{name:'RF-DETR 2XLarge'},wemm:{name:'WeMM'}});
    if(path==='/api/training/servers')return reply([{id:'default',name:'测试GPU服务器',enabled:true},{id:targetId,name:'TensorRT目标服务器',enabled:true}]);
    if(path==='/api/projects')return reply({items:[],total:0});
    if(path==='/api/training/runs')return reply({items:[run,wemm,yolo,...jobs],total:3+jobs.length});
    if(path==='/api/gpu-resources')return reply({gpus:[{uuid:'GPU-test-1',index:0,name:'A100',memory_free_mib:40000}]});
    if(path===`/api/gpu-resources/servers/${targetId}/resources`)return reply({gpus:[{uuid:'GPU-target',index:0,name:'T4',memory_free_mib:14000}]});
    if(path.endsWith('/artifacts/download'))return route.fulfill({body:'fixture-engine',contentType:'application/octet-stream',headers:{'Content-Disposition':'attachment; filename=model.engine'}});
    if(path.endsWith('/artifacts')&&jobs.some(job=>path.includes(job.id)))return reply({files:[{path:'model.onnx',bytes:20,role:'weights'},{path:'model.engine',bytes:14,role:'weights'}],truncated:false});
    if(path.endsWith('/artifacts'))return reply({files:path.includes(wemmId)?[{path:'v0/checkpoint-40/adapter_config.json',bytes:100,role:'file'}]:[{path:'checkpoint_best_total.pth',bytes:40000,role:'weights'}],truncated:false});
    if(path.endsWith('/analysis'))return reply([]);
    if(path.endsWith('/outputs')&&req.method()==='POST'){
      const body=req.postDataJSON();writes.push(body);
      if(fail)return route.fulfill({status:422,json:{detail:'动态batch须满足 min ≤ opt ≤ max'}});
      const merge=body.format==='merge',job={...run,id:'33333333-3333-4333-8333-33333333333'+jobs.length,
        name:merge?'WeMM · LoRA合并':'RFDETR · TENSORRT',kind:merge?'model_merge':'model_export',status:'draft',
        config:{...config,output:body,output_subdir:'result'},source:{kind:'model_output',run_id:merge?wemmId:id,source_path:body.source_path}};
      if(body.format==='tensorrt'){job.config.onnx_stage={server:run.server,config};job.server={id:targetId,name:'TensorRT目标服务器'};job.inspection={output_phase:'onnx'};}
      jobs.push(job);return reply(job);
    }
    throw Error('Unexpected '+req.method()+' '+path);
  });
  page.on('pageerror',e=>errors.push(e.message));
  const select=async(label,text)=>{await page.getByLabel(label,{exact:true}).click();await page.locator('.ant-select-dropdown:visible').getByText(text,{exact:true}).click();};
  try{
    await page.goto(origin+'/training');await page.setViewportSize({width:1440,height:1100});
    await page.getByRole('button',{name:run.name,exact:true}).click();
    await page.getByRole('button',{name:'导出模型',exact:true}).click();
    await page.getByLabel('训练服务器检查点路径（测试GPU服务器）',{exact:true}).fill('/data/run/checkpoint_best_total.pth');
    await select('执行GPU','0 · A100 · GPU-test-1');
    await select('输出格式','TensorRT（先导出ONNX）');
    await select('TensorRT目标GPU服务器','TensorRT目标服务器');
    await select('TensorRT构建GPU','0 · T4 · GPU-target');
    await select('ONNX导出GPU（训练服务器：测试GPU服务器）','0 · A100 · GPU-test-1');
    if(await page.getByLabel('训练服务器检查点路径（测试GPU服务器）',{exact:true}).inputValue()!=='/data/run/checkpoint_best_total.pth')throw Error('Switching target erased training checkpoint');
    await page.getByLabel('动态batch',{exact:true}).click();
    await page.getByLabel('最小batch',{exact:true}).fill('8');
    await page.getByLabel('最佳batch（ONNX导出batch）',{exact:true}).fill('7');
    await page.getByLabel('最大batch',{exact:true}).fill('8');
    await page.getByLabel('信任RF-DETR checkpoint（trust_checkpoint）',{exact:true}).check();
    await page.getByRole('button',{name:'创建导出任务',exact:true}).click();
    await page.getByText('动态batch须满足 min ≤ opt ≤ max',{exact:true}).first().waitFor();
    if(await page.getByLabel('最佳batch（ONNX导出batch）',{exact:true}).inputValue()!=='7')throw Error('Error lost form');
    fail=false;await page.getByLabel('最小batch',{exact:true}).fill('1');
    await page.screenshot({path:'output/playwright/model-output-form.png',fullPage:true,animations:'disabled'});
    await page.getByRole('button',{name:'创建导出任务',exact:true}).click();
    await page.getByRole('button',{name:'启动执行',exact:true}).waitFor();
    const body=writes.at(-1);
    if(body.format!=='tensorrt'||!body.dynamic_batch||body.opt_batch!==7||body.max_batch!==8||!body.trust_checkpoint||body.gpu_uuid!=='GPU-target'||body.source_gpu_uuid!=='GPU-test-1'||body.server_id!==targetId)throw Error('Incorrect TensorRT submission');
    if(!await page.getByRole('button',{name:'启动执行',exact:true}).isDisabled())throw Error('Start available before precheck');
    await page.getByText('导出阶段：训练服务器导出ONNX',{exact:true}).waitFor();
    await page.getByRole('tab',{name:'模型产物',exact:true}).click();
    const downloadEvent=page.waitForEvent('download');await page.getByRole('row').filter({hasText:'model.engine'}).getByRole('button',{name:'下载到本地',exact:true}).click();
    const download=await downloadEvent;if(download.suggestedFilename()!=='model.engine')throw Error('Engine download filename mismatch');
    await page.getByRole('dialog').getByRole('button',{name:/关闭|Close/}).click();
    await page.getByRole('button',{name:wemm.name,exact:true}).click();await page.getByRole('button',{name:'合并LoRA模型',exact:true}).click();
    await page.getByLabel('目标服务器adapter目录',{exact:true}).fill('/data/run/checkpoint-40');
    await select('执行GPU','0 · A100 · GPU-test-1');
    await page.getByRole('button',{name:'创建合并任务',exact:true}).click();await page.getByRole('button',{name:'启动执行',exact:true}).waitFor();
    const merged=writes.at(-1);if(merged.image!=='training-image:1'||merged.model_type!=='wemm_embedding'||merged.base_model_path!=='/data/WeMM')throw Error('Merge did not use original training config');
    await page.getByRole('dialog').getByRole('button',{name:/关闭|Close/}).click();
    await page.getByRole('button',{name:yolo.name,exact:true}).click();await page.getByRole('button',{name:'导出模型',exact:true}).click();
    await page.getByLabel('训练服务器检查点路径（测试GPU服务器）',{exact:true}).fill('/data/run/best.pt');await select('执行GPU','0 · A100 · GPU-test-1');
    await page.getByLabel('固定batch',{exact:true}).fill('4');
    await page.setViewportSize({width:390,height:844});
    await page.getByRole('button',{name:'创建导出任务',exact:true}).scrollIntoViewIfNeeded();
    await page.screenshot({path:'output/playwright/model-output-mobile.png',fullPage:true,animations:'disabled'});
    await page.getByRole('button',{name:'创建导出任务',exact:true}).click();await page.getByRole('button',{name:'启动执行',exact:true}).waitFor();
    const fixed=writes.at(-1);if(fixed.batch!==4||fixed.dynamic_batch||fixed.input_name!=='input'||fixed.output_name!=='output'||fixed.opset!==13)throw Error('YOLO fixed batch or naming lost');
    if(errors.length)throw Error(errors.join(';'));
    return {checks:['ONNX source A100 and target T4 are submitted separately; target change preserves checkpoint','TensorRT dynamic batch 1/7/8 and checkpoint trust','Validation error preserves form','Export phase and engine download','Created task uses precheck/start lifecycle','WeMM merge preserves training image and base model','YOLO fixed batch4, input/output names and opset13; mobile form submits'],fixtureWrites:writes.length,businessWrites:0};
  }catch(error){await page.screenshot({path:'output/playwright/model-output-ui-error.png',fullPage:true});throw Error(error.message+'; writes='+JSON.stringify(writes)+'; buttons='+JSON.stringify(await page.getByRole('button').allTextContents())+'; errors='+JSON.stringify(errors));}
  finally{await page.goto('about:blank');await page.unrouteAll({behavior:'ignoreErrors'});}
}
