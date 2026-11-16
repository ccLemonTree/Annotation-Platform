async page=>{
  const base='http://127.0.0.1:3017',id='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',referenceId='bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb';
  const vl='cccccccc-cccc-4ccc-8ccc-cccccccccccc',det='dddddddd-dddd-4ddd-8ddd-dddddddddddd';
  const project={id,project_name:'检测范例测试',task_type:'detection',label_schema:{labels:['person']},status:'active'};
  const writes=[],errors=[];
  await page.addInitScript(()=>{if(location.protocol==='http:')localStorage.setItem('token','isolated-detection-example');});
  page.on('pageerror',error=>errors.push(error.message));
  await page.route(base+'/api/**',async route=>{
    const request=route.request(),path=new URL(request.url()).pathname;
    if(request.method()!=='GET'){
      if(path!=='/api/automation/jobs')throw Error('Unexpected write '+path);
      writes.push(request.postDataJSON());return route.fulfill({json:{id:'job-fixture',status:'pending'}});
    }
    let data;
    if(path==='/api/auth/me')data={id:1,role:'admin',username:'fixture',tenant_id:1};
    else if(path==='/api/projects')data={items:[project],total:1};
    else if(path===`/api/projects/${id}`)data=project;
    else if(path==='/api/model-services')data=[{id:vl,key:'vl',name:'视觉预标注',kind:'caption',protocol:'openai_chat',enabled:true,model_name:'test-vl'},
      {id:det,key:'det',name:'常规检测',kind:'detection',protocol:'model_service',enabled:true,labels:['person'],label_mapping:{},confidence:.3,iou:.5}];
    else if(path.endsWith('/preannotation-examples'))data={items:[{asset_id:referenceId,original_name:'manual-boxes.png',annotation_version:2,compatible:true,task_type:'detection',width:64,height:48,boxes:[{label:'person',x:8,y:8,width:20,height:24}]}],total:1};
    else if(path.endsWith('/content'))return route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="64" height="48"><rect width="64" height="48" fill="#333"/></svg>'});
    else if(path==='/api/automation/jobs'||path==='/api/automation/review-queue')data={items:[],total:0};
    else throw Error('Unmocked API '+path);
    return route.fulfill({json:data});
  });
  await page.goto(base+`/automation?project_id=${id}`);
  await page.getByRole('button',{name:'创建任务',exact:true}).click();
  const form=page.getByRole('dialog',{name:'创建预标注 / AI审核任务',exact:true});
  await form.getByRole('checkbox',{name:'大模型按正确范例预标注检测框',exact:true}).check();
  await form.getByRole('combobox',{name:'范例预标注大模型',exact:true}).click();
  await page.getByText('视觉预标注 · test-vl',{exact:true}).click();
  await form.getByRole('button',{name:'选择正确范例（可选）',exact:true}).click();
  const picker=page.getByRole('dialog',{name:'选择人工正确范例',exact:true});
  await picker.getByText('manual-boxes.png · v2',{exact:true}).waitFor();
  await picker.getByRole('button',{name:'预览标注',exact:true}).click();
  const preview=page.getByRole('dialog',{name:'正确范例标注预览',exact:true});
  if(await preview.getByRole('img',{name:'人工正确范例检测框',exact:true}).locator('rect').count()!==1)throw Error('Manual boxes overlay missing');
  await page.setViewportSize({width:390,height:844});
  await preview.waitFor({state:'visible'});
  await page.waitForTimeout(350);
  await page.screenshot({path:'output/playwright/detection-example-preview-mobile.png',fullPage:true});
  await preview.getByRole('button',{name:'关闭标注预览',exact:true}).click();
  await picker.getByRole('button',{name:'使用此范例',exact:true}).click();
  await form.getByRole('button',{name:'提交任务',exact:true}).click();
  await form.waitFor({state:'hidden'});
  const posted=writes[0];
  if(posted.preannotation_model_id!==vl||posted.detector_id||posted.example_asset_id!==referenceId||posted.example_annotation_version!==2||posted.config.auto_review!==false)throw Error('Detection example/model/version not submitted correctly: '+JSON.stringify(posted));
  if(errors.length)throw Error(errors.join('\n'));
  return {passed:true,detection_example:true,manual_box_preview:true,llm_selected:true,example_version:true,manual_review_required:true,mobile:true,errors};
}
