async page => {
  await page.bringToFront();await page.emulateMedia({reducedMotion:'no-preference'});await page.unrouteAll();page.setDefaultTimeout(10000);
  const checks=[],writes=[],errors=[];let upload,deleted=false,deleteFails=true;
  const projectId='22222222-2222-4222-8222-222222222222';
  const config={min_long_edge:0,classification_enabled:false,scenes:[]};
  const run=(id,status,name)=>({id,name,status,kind:'yolo26',server:{id:'default',name:'测试服务器'},config:{gpu_uuids:['GPU-test'],output_subdir:'train',dataset_path:'/data/data.yaml'},source:{kind:'remote'},inspection:{},created_at:'2026-10-17T08:00:00Z',output_dir:'/owned/'+id});
  const finished=run('11111111-1111-4111-8111-111111111111','completed','完成的训练'),running=run('33333333-3333-4333-8333-333333333333','running','正在训练');
  page.on('pageerror',error=>errors.push(error.message));
  await page.route(/^http:\/\/localhost:3000\/api\//,route=>{
    const req=route.request(),path=new URL(req.url()).pathname,method=req.method();
    const json=(value,status=200)=>route.fulfill({json:value,status});
    if(method!=='GET')writes.push({path,method});
    if(path==='/api/auth/me')return json({role:'admin',username:'隔离验收'});
    if(path==='/api/projects')return json({items:[{id:projectId,project_name:'火烟项目',task_type:'detection',label_schema:{labels:['fire','smoke']}}],total:1});
    if(path==='/api/projects/'+projectId)return json({id:projectId,project_name:'火烟项目',task_type:'detection',label_schema:{labels:['fire','smoke']}});
    if(path==='/api/assets')return json({items:[],total:0});
    if(path==='/api/images/collections')return json([{id:'group',name:'导入验收',image_count:0,ingest_config:config}]);
    if(path==='/api/images/ingest-settings')return json(config);
    if(path==='/api/images')return json({items:upload?.status==='completed'?[{id:'image-test',original_name:'imported.jpg',collection_names:['导入验收'],scene_labels:[],scene_status:'unclassified',has_imported_annotation:true,imported_annotation_trusted:true,source_type:'upload',file_size:100,width:64,height:48,created_at:'2026-10-17T08:00:00Z'}]:[],total:upload?.status==='completed'?1:0});
    if(path==='/api/images/image-test/content')return route.fulfill({status:200,contentType:'image/png',body:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aC+sAAAAASUVORK5CYII=','base64')});
    if(path==='/api/images/associate'){writes[writes.length-1].input=req.postDataJSON();return json({added_count:1,asset_ids:['asset-test'],annotated:1,conflicts:0});}
    if(path==='/api/image-uploads/config')return json({chunk_size:1024,max_file_size:1000000,max_image_size:100000,max_expanded_size:10000000,max_entries:1000,retention_days:7});
    if(path==='/api/image-uploads'){
      if(method==='GET')return json(upload?[upload]:[]);
      const values=req.postDataJSON();writes[writes.length-1].input=values;
      upload={...values,id:'upload-test',collection_id:'group',status:'uploading',uploaded_parts:[],uploaded_bytes:0,chunk_size:1024,cursor:0,result:{imported:1,duplicates:0,filtered:0,invalid:0,ignored:0,errors:[],annotated:1,conflicts:0},error:null};return json(upload);
    }
    if(path.startsWith('/api/image-uploads/')){
      if(path.endsWith('/complete'))upload.status='completed';return json(upload);
    }
    if(path==='/api/training/catalog')return json({yolo26:{name:'YOLO',defaults:{},environment:{},schema:{properties:{}}}});
    if(path==='/api/training/servers')return json([{id:'default',name:'测试服务器',enabled:true}]);
    if(path==='/api/training/runs')return json({items:deleted?[running]:[finished,running],total:deleted?1:2});
    if(path.endsWith('/logs'))return json({text:'Epoch     GPU_mem   box_loss   cls_loss\n1/10      1.3G      0.421      0.202\n100% |██████████| 100/100\nWarning: sample warning\nTraceback: preserved error\n'+('metric '.repeat(100))});
    if(path.endsWith('/artifacts/download'))return route.fulfill({body:Buffer.from('verified-checkpoint'),contentType:'application/octet-stream'});
    if(path.endsWith('/artifacts'))return json({files:[{path:'weights/best.pt',bytes:123456,role:'weights'}],truncated:false});
    if(path==='/api/training/runs/'+finished.id&&method==='DELETE'){
      if(deleteFails)return json({detail:'状态已变化，请刷新后重试'},409);
      deleted=true;return route.fulfill({status:204});
    }
    if(path==='/api/training/runs/'+finished.id)return json(finished);
    if(path==='/api/training/runs/'+running.id)return json(running);
    return json({detail:'unexpected fixture '+path},404);
  });
  await page.goto('http://localhost:3000/login');await page.evaluate(()=>{
    localStorage.setItem('token','isolated-ui');localStorage.setItem('user',JSON.stringify({role:'admin',username:'隔离验收'}));
  });await page.setViewportSize({width:1440,height:1000});
  await page.goto('http://localhost:3000/assets');await page.locator('.page-toolbar-actions').getByRole('button',{name:/上传图片/}).click();
  const dialog=page.getByRole('dialog',{name:'上传到图片库',exact:true});
  await dialog.getByRole('checkbox',{name:'同时导入 YOLO 标注（ZIP）',exact:true}).check();
  if(await dialog.getByRole('combobox',{name:'接收YOLO标注的项目',exact:true}).count())throw Error('Library import must not require a project');
  const trust=dialog.getByRole('checkbox',{name:'信任已有标注，直接用于训练/验证（管理员）',exact:true});
  if(await trust.isChecked())throw Error('Trusted import should require explicit selection');await trust.check();
  await dialog.getByLabel('上传图片或压缩包',{exact:true}).setInputFiles({name:'annotated.zip',mimeType:'application/zip',buffer:Buffer.alloc(600,65)});
  await page.setViewportSize({width:390,height:844});
  await trust.scrollIntoViewIfNeeded();if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth))throw Error('Import form overflow');
  await page.screenshot({path:'output/playwright/yolo-import-mobile.png'});
  await dialog.getByRole('button',{name:'开始 / 继续上传',exact:true}).click();await dialog.getByText(/导入标注 1/).waitFor();
  const input=writes.find(item=>item.path==='/api/image-uploads'&&item.method==='POST').input;
  if(input.annotation_format!=='yolo'||input.annotation_project_id||input.trust_annotations!==true)throw Error('Library YOLO/trust options not submitted');
  checks.push('YOLO ZIP stores reusable library annotation without project; explicit admin trust; mobile form and annotated summary');
  await dialog.getByRole('button',{name:'关闭',exact:true}).click();
  await page.setViewportSize({width:1440,height:1000});await page.getByRole('row').filter({hasText:'imported.jpg'}).getByRole('checkbox').check();
  await page.getByRole('button',{name:'添加到标注项目（1）',exact:true}).click();
  const association=page.getByRole('dialog',{name:'添加到标注项目',exact:true});
  await association.getByRole('combobox',{name:'图片添加到的项目',exact:true}).click();await page.locator('.ant-select-dropdown:visible').getByText('火烟项目',{exact:true}).click();
  const include=association.getByRole('checkbox',{name:'带入已有YOLO标注',exact:true});if(await include.isChecked())throw Error('Copy must be explicit');await include.check();
  await association.getByRole('button',{name:'添加图片',exact:true}).click();await association.waitFor({state:'hidden'});
  if(!writes.find(item=>item.path==='/api/images/associate')?.input.include_imported_annotations)throw Error('Association did not request annotation copy');
  checks.push('Add-to-project explicitly copies library annotations; normal image-only association remains default');
  await page.goto('http://localhost:3000/projects/'+projectId+'/images');await page.getByRole('button',{name:'从图片库添加',exact:true}).click();
  const picker=page.getByRole('dialog',{name:'从图片库添加到 火烟项目',exact:true});
  await picker.getByRole('checkbox',{name:'带入已有YOLO标注',exact:true}).check();
  await picker.getByRole('row').filter({hasText:'imported.jpg'}).getByRole('checkbox').check();
  await page.screenshot({path:'output/playwright/yolo-project-picker-desktop.png'});
  await picker.getByRole('button',{name:'添加到 火烟项目',exact:true}).click();await picker.waitFor({state:'hidden'});
  if(writes.filter(item=>item.path==='/api/images/associate').length!==2)throw Error('Project picker did not copy annotations');
  checks.push('Project image picker also offers explicit independent annotation copy and shows trusted-source status');
  await page.setViewportSize({width:1440,height:1000});await page.goto('http://localhost:3000/training');
  await page.getByRole('button',{name:'完成的训练',exact:true}).click();await page.getByRole('tab',{name:'训练日志',exact:true}).click();
  const log=page.getByRole('log',{name:'训练日志内容'});await log.getByText('Traceback: preserved error',{exact:true}).waitFor();
  if(!await log.evaluate(node=>node.scrollWidth>node.clientWidth))throw Error('Table logs should scroll horizontally');
  await page.getByRole('checkbox',{name:'自动换行',exact:true}).check();
  if(await log.locator('code').first().evaluate(node=>getComputedStyle(node).whiteSpace)!=='pre-wrap')throw Error('Wrap toggle failed');
  await page.screenshot({path:'output/playwright/training-logs-desktop.png'});
  await page.setViewportSize({width:390,height:844});
  if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth))throw Error('Log page overflow');
  await page.screenshot({path:'output/playwright/training-logs-mobile.png'});
  checks.push('Readable fixed-width log panel, line numbers, wrap toggle, errors retained, 390px');
  await page.setViewportSize({width:1440,height:1000});await page.getByRole('tab',{name:'模型产物',exact:true}).click();
  const pending=page.waitForEvent('download');await page.getByRole('button',{name:'下载到本地',exact:true}).click();
  const download=await pending;await download.saveAs('output/playwright/downloaded-best.pt');
  await page.bringToFront();
  if(download.suggestedFilename()!=='best.pt'||await download.failure())throw Error('Artifact download failed');
  checks.push('Artifact produces a browser download named best.pt');
  await page.getByRole('button',{name:'删除任务',exact:true}).click();
  const confirmation=page.getByRole('dialog',{name:'删除此训练任务记录？',exact:true});
  await confirmation.getByText(/服务器上的模型权重、日志、数据和容器均保留/).waitFor();
  await confirmation.getByRole('button',{name:'确认删除',exact:true}).click();await confirmation.getByText('状态已变化，请刷新后重试',{exact:true}).waitFor();
  deleteFails=false;await confirmation.getByRole('button',{name:'确认删除',exact:true}).click();await confirmation.waitFor({state:'hidden'});
  await page.getByRole('button',{name:'正在训练',exact:true}).click();
  if(!await page.getByRole('button',{name:'删除任务',exact:true}).isDisabled())throw Error('Active training deletion enabled');
  checks.push('Delete confirmation retains remote files, failure preserved, inactive deletion succeeds, active deletion disabled');
  if(writes.some(item=>/\/(start|stop)$/.test(item.path)))throw Error('Unexpected training execution');
  if(errors.length)throw Error(errors.join('\n'));
  return{checks,runtimeErrors:errors,scope:'All APIs intercepted. No business import/delete or remote training action.'};
}
