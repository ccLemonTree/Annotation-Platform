async page => {
  await page.unrouteAll(); page.setDefaultTimeout(10000);
  const checks=[],writes=[],errors=[];let fail=false,held=false;
  const runId='11111111-1111-4111-8111-111111111111',setId='22222222-2222-4222-8222-222222222222';
  const training={id:runId,name:'RF-DETR 已完成训练',kind:'rfdetr_medium',status:'completed',server:{id:'default',name:'训练服务器'},output_dir:'/ai/chen/runs/'+runId,
    config:{output_subdir:'train',image:'local/detection:fixed',selected_labels:['fire','smoke'],accept_platform_model_license:false},source:{},inspection:{}};
  const metrics={coverage:{total:1,succeeded:0,failed:1,pending:0,canceled:0,complete:false}};
  const policy={inference_confidence:.001,confidence:.25,match_iou:.5,nms_iou:.45,labels:[],label_mapping:{}};
  const run=()=>({id:'validation-1',set_id:setId,name:'权重测试',status:'failed',total:1,processed:1,failed_count:1,remote_active:held,model:{id:'a'.repeat(64),name:'checkpoint_best_total.pth',source:'checkpoint',weight_path:'/models/checkpoint_best_total.pth',weight_sha256:'a'.repeat(64),image_digest:'sha256:'+'b'.repeat(64),kind:'rfdetr_medium',device:'cpu',revision:1},config:policy,metrics});
  page.on('pageerror',error=>errors.push(error.message));
  await page.route(/^http:\/\/localhost:3000\/api\//,route=>{
    const req=route.request(),path=new URL(req.url()).pathname,method=req.method();
    const json=(value,status=200)=>route.fulfill({json:value,status});
    if(path==='/api/auth/me')return json({role:'admin',username:'隔离验证'});
    if(path==='/api/projects')return json({items:[{id:'project',project_name:'验证项目',task_type:'detection'}],total:1});
    if(path==='/api/validation/sets')return json({items:[{id:setId,name:'火烟真值',labels:['fire','smoke'],total:1,source:{kind:'project'}}],total:1});
    if(path==='/api/training/servers')return json([{id:'default',name:'训练服务器',enabled:true}]);
    if(path==='/api/training/runs')return json({items:[training,{...training,id:'active',name:'正在训练',status:'running'}],total:2});
    if(path.endsWith('/artifacts'))return json({files:[{path:'checkpoint_best_total.pth',bytes:1000000,role:'weights'},{path:'logs.json',bytes:10,role:'file'}],truncated:false});
    if(path==='/api/gpu-resources')return json({gpus:[{index:0,uuid:'GPU-free',name:'测试GPU'}]});
    if(path==='/api/validation/runs'){
      if(method==='POST'){writes.push(req.postDataJSON());if(!fail)Object.assign(policy,req.postDataJSON().config);return fail?json({detail:'权重不存在'},400):json(run(),201);}
      return json({items:[run()],total:1});
    }
    if(path==='/api/validation/runs/validation-1')return json(run());
    if(path.endsWith('/items'))return json({items:[],total:0});
    return json({detail:'fixture unexpected '+path},404);
  });
  await page.goto('http://localhost:3000/login');
  await page.evaluate(()=>{localStorage.setItem('token','checkpoint-fixture');localStorage.setItem('user',JSON.stringify({role:'admin',username:'隔离验证'}));});
  await page.emulateMedia({reducedMotion:'reduce'});await page.setViewportSize({width:1440,height:1000});
  await page.goto('http://localhost:3000/validation');
  await page.getByRole('button',{name:'验证权重',exact:true}).click();
  const dialog=page.getByRole('dialog',{name:'验证训练权重',exact:true});
  if(await dialog.getByRole('spinbutton',{name:'任务交集阈值（%）',exact:true}).inputValue()!=='20')throw Error('New task overlap did not default to 20 percent');
  await dialog.getByRole('spinbutton',{name:'任务交集阈值（%）',exact:true}).fill('35');
  const choose=async(name,label)=>{
    const input=dialog.getByRole('combobox',{name,exact:true});await input.scrollIntoViewIfNeeded();
    await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
    await input.click();
    const options=await page.locator('.ant-select-dropdown:visible .ant-select-item-option-content').allTextContents();
    const index=options.indexOf(label);if(index<0)throw Error('Missing option '+label);
    await input.press('Home');for(let i=0;i<index;i++)await input.press('ArrowDown');await input.press('Enter');
  };
  await choose('权重训练任务','RF-DETR 已完成训练 · rfdetr_medium · 已完成');
  await dialog.getByRole('combobox',{name:'训练权重文件',exact:true}).click();
  if(await page.locator('.ant-select-dropdown:visible').getByText(/logs.json/).count())throw Error('Non-weight artifact selectable');
  await dialog.getByRole('combobox',{name:'训练权重文件',exact:true}).press('Enter');
  await dialog.getByRole('button',{name:'开始验证',exact:true}).click();await dialog.waitFor({state:'hidden'});
  if(writes[0].model_id || writes[0].checkpoint.training_run_id!==runId || writes[0].checkpoint.class_names.join(',')!=='fire,smoke')throw Error('Trained weight source not submitted');
  if(writes[0].config.target_overlap!==.35||writes[0].config.match_iou!==.5)throw Error('Overlap percent was not saved independently of standard IoU');
  checks.push('completed training artifact, server, model kind and class order submitted without service ID');
  await page.getByRole('button',{name:'验证权重',exact:true}).click();
  await choose('权重来源','服务器已有权重路径');
  await dialog.getByRole('textbox',{name:'服务器权重路径',exact:true}).fill('/models/best.pt');
  await dialog.getByRole('spinbutton',{name:'任务交集阈值（%）',exact:true}).fill('45');
  await dialog.getByRole('button',{name:'读取显卡',exact:true}).click();
  await choose('权重验证设备','GPU 0 · 测试GPU');
  fail=true;await dialog.getByRole('button',{name:'开始验证',exact:true}).click();
  await dialog.getByText('验证任务创建失败：权重不存在',{exact:true}).waitFor();
  if(await dialog.getByRole('textbox',{name:'服务器权重路径',exact:true}).inputValue()!=='/models/best.pt')throw Error('Failed save lost checkpoint input');
  if(await dialog.getByRole('spinbutton',{name:'任务交集阈值（%）',exact:true}).inputValue()!=='45')throw Error('Failed save lost overlap threshold');
  checks.push('manual checkpoint/GPU option and failed creation preserve inputs');
  await page.setViewportSize({width:390,height:844});
  await dialog.getByRole('button',{name:'开始验证',exact:true}).scrollIntoViewIfNeeded();
  if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth))throw Error('Mobile form overflow');
  await page.screenshot({path:'output/playwright/checkpoint-form-mobile.png'});
  fail=false;await dialog.getByRole('button',{name:'开始验证',exact:true}).click();await dialog.waitFor({state:'hidden'});
  held=true;await page.getByRole('button',{name:'刷新任务',exact:true}).click();
  await page.getByRole('button',{name:'确认停止验证',exact:true}).waitFor();
  if(!await page.getByRole('button',{name:'重试失败项',exact:true}).isDisabled())throw Error('Retry allowed with unconfirmed remote job');
  await page.getByRole('button',{name:'查看结果',exact:true}).click();
  await page.getByText(/SHA256/).waitFor();
  const threshold=page.getByRole('spinbutton',{name:'交集阈值（%）',exact:true});
  if(await threshold.inputValue()!=='45')throw Error('Report did not use saved task overlap');
  await threshold.fill('60');await threshold.press('Tab');
  await page.getByRole('button',{name:'关闭结果',exact:true}).click();
  await page.getByRole('button',{name:'查看结果',exact:true}).click();
  await page.getByText(/SHA256/).waitFor();
  if(await threshold.inputValue()!=='45')throw Error('Reopened report did not restore saved task overlap');
  checks.push('20% creation default, independent percent payload, failed-save retention and report reopen use saved task threshold');
  if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth))throw Error('Mobile weight metadata overflow');
  checks.push('mobile form/report and unconfirmed remote reservation prevents retry');
  if(errors.length)throw Error(errors.join(';'));
  await page.goto('about:blank');await page.unrouteAll();
  return{checks,writeFixtures:writes.length,runtimeErrors:errors,scope:'All APIs intercepted; no business validation or model call.'};
}
