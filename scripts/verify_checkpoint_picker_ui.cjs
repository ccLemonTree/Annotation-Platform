async page=>{
  await page.unrouteAll();page.setDefaultTimeout(8000);
  const origin='http://192.168.1.90:3000',pattern=/^http:\/\/192\.168\.1\.90:3000\/api\//;
  const id='11111111-1111-4111-8111-111111111111',failed='22222222-2222-4222-8222-222222222222';
  const root='/runs/owned',checks=[],errors=[];let available=true,refreshes=0,submitted;
  page.on('pageerror',error=>errors.push(error.message));
  const run=(id,name,status)=>({id,name,status,kind:'rfdetr_2xlarge',output_dir:root,server:{id:'default',name:'Fixture'},
    source:{},inspection:{},config:{output_subdir:'train',image:'fixture-image',selected_labels:['fire','smoke','star'],accept_platform_model_license:true}});
  await page.route(pattern,async route=>{
    const request=route.request(),path=new URL(request.url()).pathname;
    const json=(body,status=200)=>route.fulfill({json:body,status});
    if(path==='/api/auth/me')return json({id:1,username:'fixture',role:'admin',is_active:true});
    if(path==='/api/model-services')return json([]);
    if(path==='/api/model-previews/config')return json({max_images:5000,max_upload_size:10*1024**3,max_expanded_size:20*1024**3,max_image_size:100*1024**2});
    if(path==='/api/model-previews'&&request.method()==='GET')return json({items:[],total:0});
    if(path==='/api/training/servers')return json([{id:'default',name:'Fixture',enabled:true}]);
    if(path==='/api/training/runs')return json({items:[run(id,'Running detector','running'),run(failed,'Failed detector','failed')],total:2});
    if(path.endsWith('/artifacts')){
      refreshes++;return json({files:path.includes(failed)||!available?[]:[{path:'checkpoint_best_ema.pth',bytes:507931474,role:'weights'}],truncated:false});
    }
    if(path==='/api/model-previews'&&request.method()==='POST'){
      submitted=JSON.parse(request.postDataBuffer().toString('utf8').match(/name="request"\r\n\r\n([^\r]+)/)[1]);
      return json({detail:'fixture: request captured'},400);
    }
    throw Error('Unexpected API '+request.method()+' '+path);
  });
  const select=async(label,text)=>{await page.getByRole('combobox',{name:label,exact:true}).click();await page.getByText(text,{exact:true}).last().click();};
  try{
    await page.goto(origin+'/model-preview');
    await select('预览模型来源','YOLO / RF-DETR 训练权重');
    await page.getByRole('combobox',{name:'权重训练任务'}).click();
    await page.getByText('Running detector · rfdetr_2xlarge · 运行中',{exact:true}).last().click();
    await select('训练权重文件','checkpoint_best_ema.pth · 484.4 MiB');
    await page.getByText(/训练仍在进行/).waitFor();
    const png=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jUKcAAAAASUVORK5CYII=','base64');
    await page.getByLabel('选择模型预览图片',{exact:true}).setInputFiles({name:'owned.png',mimeType:'image/png',buffer:png});
    await page.getByRole('button',{name:'开始模型预览',exact:true}).click();
    await page.waitForFunction(()=>document.body.textContent.includes('fixture: request captured'));
    if(submitted.checkpoint.training_run_id!==id||submitted.checkpoint.weight_path!==root+'/train/checkpoint_best_ema.pth'||submitted.checkpoint.device!=='cpu'||submitted.checkpoint.class_names.join(',')!=='fire,smoke,star')throw Error('Wrong checkpoint payload');
    checks.push('Running checkpoint selectable with correct path, class order, training ID and CPU payload');
    available=false;await page.getByRole('button',{name:'刷新权重列表',exact:true}).click();
    await page.getByText(/尚未找到检测权重/).waitFor();
    if(!(await page.getByRole('combobox',{name:'训练权重文件'}).inputValue()===''))throw Error('Removed checkpoint remained selected');
    checks.push('Refresh clears unavailable checkpoint and explains empty state');
    await select('权重训练任务','Failed detector · rfdetr_2xlarge · 失败');
    await page.getByText(/尚未找到检测权重/).waitFor();
    await page.getByRole('button',{name:'填写服务器权重路径',exact:true}).click();
    await page.getByRole('textbox',{name:'服务器权重路径'}).waitFor();
    checks.push('Failed task empty state offers direct manual path entry');
    if(errors.length||refreshes<3)throw Error(JSON.stringify({errors,refreshes}));
    return {checks,refreshes,scope:'Real LAN UI, intercepted API and inference; no business writes'};
  }finally{await page.goto('about:blank');await page.unroute(pattern);}
}
