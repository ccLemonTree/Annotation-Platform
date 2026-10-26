async page=>{
  await page.unrouteAll();page.setDefaultTimeout(12000);
  const origin='http://192.168.1.90:3000',pattern=/^http:\/\/192\.168\.1\.90:3000\/api\//;
  const id='11111111-1111-4111-8111-111111111111',model='22222222-2222-4222-8222-222222222222';
  const grounding='55555555-5555-4555-8555-555555555555';
  const checks=[],inputs=[],writes=[],errors=[];let job,reads=0,failNext=false;
  const png=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jUKcAAAAASUVORK5CYII=','base64');
  page.on('pageerror',error=>errors.push(error.message));
  const make=(request)=>({id,set_id:id,name:'preview.png',status:'pending',total:2,processed:0,failed_count:0,error:null,created_at:'2026-10-19T03:00:00Z',updated_at:'2026-10-19T03:00:00Z',
    model:{id:request.model_id||model,revision:1,name:request.checkpoint?'RF-DETR checkpoint':request.model_id===grounding?'GroundingPI':'UI detector',...(request.checkpoint?{source:'checkpoint'}:{}),...(request.model_id===grounding?{score_type:'uncalibrated'}:{})},
    config:{inference_confidence:request.confidence,confidence:request.confidence,nms_iou:request.iou,labels:request.labels||[]},metrics:{},
    items:[{id:'33333333-3333-4333-8333-333333333333',original_name:'preview.png',width:64,height:48,status:'pending',predictions:[],truth:[],evaluation:{}},
      {id:'44444444-4444-4444-8444-444444444444',original_name:'empty.png',width:64,height:48,status:'pending',predictions:[],truth:[],evaluation:{}}]});
  await page.route(pattern,async route=>{
    const request=route.request(),url=new URL(request.url()),path=url.pathname;
    const json=(body,status=200)=>route.fulfill({json:body,status});
    if(request.method()!=='GET')writes.push(path);
    if(path==='/api/auth/me')return json({id:1,username:'preview-admin',role:'admin',is_active:true});
    if(path==='/api/model-previews/config')return json({max_images:5000,max_upload_size:10*1024**3,max_expanded_size:20*1024**3,max_image_size:100*1024**2});
    if(path==='/api/model-services')return json([{id:model,name:'UI detector',enabled:true,kind:'detection',protocol:'model_service',endpoint:'/detect/test',labels:['person']},{id:grounding,name:'GroundingPI',enabled:true,kind:'detection',protocol:'model_service',endpoint:'/detect/grounding',labels:['electric bicycle']},{id:'caption',name:'Do not call caption',enabled:true,kind:'caption'}]);
    if(path==='/api/training/servers')return json([{id:'default',name:'CPU fixture',enabled:true}]);
    if(path==='/api/training/runs')return json({items:[],total:0});
    if(path==='/api/model-previews'&&request.method()==='GET')return json({items:job?[job]:[],total:job?1:0});
    if(path==='/api/model-previews'&&request.method()==='POST'){
      if(failNext){failNext=false;return json({detail:'fixture upload failed'},502);}
      const body=request.postDataBuffer().toString('utf8'),match=body.match(/name="request"\r\n\r\n([^\r]+)/);
      if(!match)throw Error('Missing multipart JSON request');
      const input=JSON.parse(match[1]);inputs.push(input);
      if(!body.includes('filename="preview.png"'))throw Error('Missing uploaded image');
      job=make(input);reads=0;return json(job,201);
    }
    if(path.endsWith('/image'))return route.fulfill({contentType:'image/png',body:png});
    if(path==='/api/model-previews/'+id&&request.method()==='GET'){
      if(++reads>1){job={...job,status:'completed',processed:2,items:job.items.map((item,index)=>({...item,status:'completed',predictions:index?[]:[{label:'person',x:5,y:6,width:20,height:30,score:job.model.score_type==='uncalibrated'?.5:.93}]}))};}
      return json(job);
    }
    if(path==='/api/model-previews/'+id&&request.method()==='DELETE'){job=undefined;return route.fulfill({status:204});}
    throw Error('Unexpected API '+request.method()+' '+path);
  });
  const select=async(label,option)=>{await page.getByRole('combobox',{name:label,exact:true}).click();await page.getByText(option,{exact:true}).last().click();};
  const upload=()=>page.getByLabel('选择模型预览图片',{exact:true}).setInputFiles([{name:'preview.png',mimeType:'image/png',buffer:png},{name:'empty.png',mimeType:'image/png',buffer:png}]);
  try{
    await page.setViewportSize({width:1440,height:1000});await page.goto(origin+'/model-preview');
    await page.getByRole('menuitem',{name:'模型效果预览',exact:true}).waitFor();
    await select('预览检测模型','UI detector');await upload();
    await page.getByRole('spinbutton',{name:'预览置信度',exact:true}).fill('0.4');
    await page.getByRole('button',{name:'开始模型预览',exact:true}).click();
    await page.getByText('person 93.0%',{exact:true}).waitFor();
    if(inputs[0].confidence!==.4||inputs[0].model_id!==model||page.url().indexOf('run_id=')<0)throw Error('Service config or recoverable URL missing');
    const svg=page.getByRole('img',{name:'模型检测结果叠框'});
    if(await svg.locator('rect').count()!==1||await svg.getAttribute('viewBox')!=='0 0 64 48')throw Error('Wrong box geometry');
    if(await page.locator('.model-preview-picker').evaluateAll(inputs=>inputs.some(input=>getComputedStyle(input).display!=='none'))||await svg.locator('text').evaluate(el=>el.getBoundingClientRect().height>32))throw Error('File picker or small-image label layout incorrect');
    await page.getByRole('checkbox',{name:'显示检测框',exact:true}).uncheck();await svg.waitFor({state:'hidden'});
    await page.getByRole('checkbox',{name:'显示检测框',exact:true}).check();await svg.waitFor();
    checks.push('Detection-only selection, batch upload, threshold, progress, scaled overlay, confidence and original toggle');
    await page.getByRole('button',{name:/empty.png/}).click();await page.getByText('未检测到目标',{exact:true}).waitFor();
    await page.reload();await page.getByText('person 93.0%',{exact:true}).waitFor();
    checks.push('Empty detection and page refresh restore the preview without annotations');
    await page.setViewportSize({width:390,height:844});
    if(await page.locator('html').evaluate(el=>el.scrollWidth>el.clientWidth+1))throw Error('Mobile page overflow');
    await page.screenshot({path:'output/playwright/model-preview-mobile.png'});
    await page.setViewportSize({width:1440,height:1000});await page.screenshot({path:'output/playwright/model-preview-service.png'});
    await select('预览检测模型','GroundingPI');
    if(await page.getByRole('spinbutton',{name:'预览置信度',exact:true}).count()||await page.getByRole('spinbutton',{name:'预览NMS IoU',exact:true}).count())throw Error('Unsupported thresholds still editable');
    const labels=page.getByRole('combobox',{name:'预览检测标签',exact:true});await labels.fill('person');await labels.press('Enter');
    await upload();await page.getByRole('button',{name:'开始模型预览',exact:true}).click();
    await page.getByText('置信度：未提供',{exact:true}).waitFor();await page.getByRole('cell',{name:'未提供',exact:true}).waitFor();
    const unscored=inputs.at(-1);
    if('confidence'in unscored||'iou'in unscored||unscored.labels.join(';')!=='electric bicycle;person')throw Error('Unscored model request parameters incorrect');
    if((await svg.textContent()).includes('%')||await page.getByText('50.0%',{exact:true}).count())throw Error('Placeholder displayed as confidence');
    await page.screenshot({path:'output/playwright/model-preview-grounding-labels.png'});
    await page.setViewportSize({width:390,height:844});
    if(await page.locator('html').evaluate(el=>el.scrollWidth>el.clientWidth+1))throw Error('Grounding mobile overflow');
    await page.screenshot({path:'output/playwright/model-preview-grounding-mobile.png'});await page.setViewportSize({width:1440,height:1000});
    checks.push('Grounding labels override only this request; unsupported parameters omitted; historical placeholder hidden on overlay and table; mobile fits');
    await select('预览模型来源','YOLO / RF-DETR / DINOv3 训练权重');await select('权重来源','服务器已有权重路径');
    await select('权重模型类型','RF-DETR Medium');
    await page.getByRole('textbox',{name:'服务器权重路径'}).fill('/models/checkpoint_best_total.pth');
    const classes=page.getByRole('combobox',{name:'训练类别顺序'});await classes.fill('fire');await classes.press('Enter');await classes.fill('smoke');await classes.press('Enter');
    await upload();await page.getByRole('button',{name:'开始模型预览',exact:true}).click();
    await page.getByText('RF-DETR checkpoint',{exact:true}).first().waitFor();await page.getByText('person 93.0%',{exact:true}).waitFor();
    const cp=inputs.at(-1).checkpoint;
    if(cp.kind!=='rfdetr_medium'||cp.device!=='cpu'||cp.weight_path!=='/models/checkpoint_best_total.pth'||cp.class_names.join(',')!=='fire,smoke'||inputs.at(-1).model_id)throw Error('Checkpoint configuration incorrect');
    checks.push('Remote RF-DETR path, class order and CPU selection reach upload request; same result view');
    failNext=true;await upload();await page.getByRole('button',{name:'开始模型预览',exact:true}).click();
    await page.getByText(/上传或启动推理失败/).waitFor();await page.getByText('已选择 2 个文件',{exact:false}).waitFor();
    checks.push('Failed start keeps selected images for retry');
    await page.goto(origin+'/model-preview?model_id='+grounding);
    await page.getByText('electric bicycle',{exact:true}).waitFor();
    await page.getByText('此模型不提供检测置信度，也不执行 NMS；保留全部检测结果。',{exact:true}).waitFor();
    if(await page.getByRole('spinbutton',{name:'预览置信度',exact:true}).count())throw Error('Initial URL model did not apply capabilities');
    checks.push('Model deep link initializes default labels and capabilities after models load');
    if(errors.length||writes.some(path=>!path.startsWith('/api/model-previews')))throw Error(JSON.stringify({errors,writes}));
    return {checks,createRequests:inputs.length,businessWrites:0,scope:'Real LAN UI; controlled inference and upload responses; no model or annotation writes'};
  }catch(error){await page.screenshot({path:'output/playwright/model-preview-ui-error.png'});throw Error(error.message+'; completed checks: '+checks.join('; '));}
  finally{await page.goto('about:blank');await page.unroute(pattern);}
}
