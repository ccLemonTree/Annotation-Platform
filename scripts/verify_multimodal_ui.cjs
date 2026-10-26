async page => {
  await page.bringToFront();await page.emulateMedia({reducedMotion:'no-preference'});await page.unrouteAll();page.setDefaultTimeout(10000);
  const errors=[],checks=[],writes=[],reads=[],exports=[];let exportFailure='';page.on('pageerror',e=>errors.push(e.message));
  const ids={classification:'11111111-1111-4111-8111-111111111111',caption:'22222222-2222-4222-8222-222222222222',vector:'33333333-3333-4333-8333-333333333333',detection:'44444444-4444-4444-8444-444444444444'};
  const a1='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',a2='bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb';
  const png=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aC+sAAAAASUVORK5CYII=','base64');
  const projects=Object.entries(ids).map(([task_type,id])=>({id,task_type,tenant_id:1,project_name:{classification:'口罩分类',caption:'描述项目',vector:'向量项目',detection:'检测项目'}[task_type],label_schema:{labels:['口罩','黑衣'],classification_mode:'multi'},total_assets:2,annotated_count:0,reviewed_count:0,created_at:new Date().toISOString()}));
  const asset=(id,project=ids.vector)=>({id,project_id:project,image_id:id,original_name:id===a1?'person-one.png':'person-two.png',width:640,height:480,source_type:id===a2?'model_crop':'upload',annotation_status:'pending',created_at:new Date().toISOString()});
  const models=['YOLO','RF-DETR','SAM3','Qwen3.8-27B','WEMM'].map((name,i)=>({id:'model-'+i,name,kind:i<3?'detection':i===3?'caption':'embedding',enabled:true,revision:1,labels:['person'],protocol:i<3?'model_service':i===3?'openai_chat':'openai_embedding'}));
  let annotation,job,query;
  await page.route(/^http:\/\/localhost:3000\/api\//,async route=>{
    const req=route.request(),url=new URL(req.url()),path=url.pathname,method=req.method();const json=(data,status=200)=>route.fulfill({json:data,status});
    if(method!=='GET')writes.push({path,method,input:req.postData()?.startsWith('{')?req.postDataJSON():undefined});
    else reads.push(path);
    if(path.endsWith('/content'))return route.fulfill({contentType:'image/png',body:png});
    if(path==='/api/projects'){
      if(method==='POST')return json(projects[2],201);
      return json({items:projects,total:4});
    }
    if(path==='/api/model-services')return json(models);
    if(path==='/api/images')return json({items:[asset(a1),asset(a2)],total:2});
    if(path==='/api/images/batch-upload')return json([asset(a1)],201);
    if(path.endsWith('/stats'))return json({total_assets:2,annotated_count:0,reviewed_count:0});
    if(path==='/api/assets'){
      const project=projects.find(p=>p.id===url.searchParams.get('project_id'));
      let items=[asset(a1,project.id),asset(a2,project.id)];
      if(url.searchParams.get('scope')==='annotation'&&project.label_schema.annotation_scope==='crops')items=items.filter(a=>a.source_type==='model_crop');
      if(url.searchParams.get('image_kind')==='crops')items=items.filter(a=>a.source_type==='model_crop');
      if(url.searchParams.get('image_kind')==='originals')items=items.filter(a=>a.source_type!=='model_crop');
      return json({items,total:items.length});
    }
    if(path==='/api/assets/'+a1+'/neighbors')return json({previous_id:null,next_id:null,position:1,total:1});
    if(path==='/api/assets/'+a1)return json(asset(a1,ids.classification));
    if(path==='/api/assets/'+a2)return json(asset(a2,ids.caption));
    if(path==='/api/assets/'+a2+'/neighbors')return json({previous_id:null,next_id:null,position:1,total:1});
    if(path==='/api/annotations'){
      if(method==='POST'){annotation={...req.postDataJSON(),id:'ann',version:1,is_latest:true,review_status:'pending',created_at:new Date().toISOString()};return json(annotation,201);}
      return json({items:annotation&&url.searchParams.get('asset_id')===a1?[annotation]:[],total:annotation&&url.searchParams.get('asset_id')===a1?1:0});
    }
    if(path==='/api/annotations/ann/review'){annotation.review_status='approved';return json(annotation);}
    if(path==='/api/annotations/ann'){annotation={...annotation,...req.postDataJSON(),version:annotation.version+1};return json(annotation);}
    if(path.endsWith('/work/jobs')){
      if(method==='POST'){const body=req.postDataJSON();job={id:'job',...body,total:1,revision:1,counts:{pending:1}};return json(job,201);}
      return json(job?[job]:[]);
    }
    if(path.endsWith('/work/jobs/job'))return json({...job,page:1,items:[{id:'item',asset_id:a1,status:'pending',result:null}]});
    if(/\/work\/jobs\/job\/(retry|cancel)$/.test(path))return json({changed_count:1});
    if(path.endsWith('/matches/search')){const body=req.postDataJSON();query={id:'query',query:body,space:{revision:1},version:1,hits:[a1,a2].map((id,i)=>({asset_id:id,image_id:id,source_type:id===a2?'model_crop':'upload',score:.9-i*.1,judgment:'unknown',description:i?'白色上衣':'黑色衣服',labels:i?[]:['黑衣'],original_name:'person.png'}))};return json(query,201);}
    if(path.endsWith('/matches/query')){const body=req.postDataJSON();query={...query,version:query.version+1,hits:query.hits.map(h=>h.asset_id===body.asset_id?{...h,judgment:body.judgment}:h)};return json(query);}
    if(path.endsWith('/matches'))return json(query?[query]:[]);
    if(path.startsWith('/api/export/')){exports.push(Object.fromEntries(url.searchParams));if(exportFailure){const detail=exportFailure;exportFailure='';return json({detail},400);}return route.fulfill({contentType:'application/zip',body:Buffer.from('owned-browser-export')});}
    const project=projects.find(p=>path==='/api/projects/'+p.id);if(project){if(method==='PUT')project.label_schema=req.postDataJSON().label_schema;return json(project);}
    return json({detail:'Unexpected mock path '+path},404);
  });
  await page.goto('http://localhost:3000/login');await page.evaluate(()=>{localStorage.clear();localStorage.setItem('token','isolated');localStorage.setItem('user',JSON.stringify({id:1,tenant_id:1,username:'隔离验收',role:'admin'}));});
  await page.setViewportSize({width:1440,height:1000});
  await page.goto('http://localhost:3000/annotate/'+a1);
  await page.getByRole('checkbox',{name:'口罩',exact:true}).check();await page.getByRole('checkbox',{name:'黑衣',exact:true}).check();
  await page.locator('#image-annotation-description').fill('佩戴口罩，黑色衣服');await page.getByRole('button',{name:'保存标注',exact:true}).click();
  await page.getByText('v1 · 待审核',{exact:true}).waitFor();await page.getByRole('button',{name:'审核通过',exact:true}).click();await page.getByText('v1 · 已通过',{exact:true}).waitFor();
  if(annotation.annotation_data.labels.length!==2)throw Error('Multi-labels lost');
  await page.screenshot({path:'output/playwright/multimodal-classification.png'});checks.push('Multi-label image editor saves shared description, version and review');
  await page.goto('http://localhost:3000/annotations?project_id='+ids.caption);
  await page.getByRole('combobox',{name:'处理模型',exact:true}).click();await page.locator('.ant-select-dropdown:visible').getByText('Qwen3.8-27B · r1',{exact:true}).click();
  await page.getByRole('textbox',{name:'本批描述提示词',exact:true}).fill('描述人的衣服和口罩，不推测不可见内容');
  await page.getByRole('row').filter({hasText:'person-one.png'}).getByRole('checkbox').check();await page.getByRole('button',{name:/生成描述 · 已选 1 张/}).click();
  if(job.operation!=='caption'||!job.prompt.includes('口罩'))throw Error('Caption batch prompt lost');checks.push('Project caption model and editable batch prompt');
  await page.goto('http://localhost:3000/annotations?project_id='+ids.vector);
  if(await page.getByText('向量检索与匹配标注',{exact:true}).count())throw Error('Project management must not render the vector annotation workspace');
  if(reads.includes('/api/projects/'+ids.vector+'/matches'))throw Error('Management must not load matching queries');
  await page.getByRole('button',{name:'开始标注',exact:true}).click();
  await page.waitForURL('**/annotate/project/'+ids.vector);
  await page.getByRole('heading',{name:'向量标注工作台',exact:true}).waitFor();
  if(await page.getByRole('combobox',{name:'处理方式',exact:true}).count())throw Error('Annotation workspace must not include preparation/job controls');
  await page.getByRole('radiogroup',{name:'标注方式',exact:true}).getByText('文搜图标注',{exact:true}).click();
  await page.getByRole('combobox',{name:'检索向量模型',exact:true}).click();await page.locator('.ant-select-dropdown:visible').getByText('WEMM · r1',{exact:true}).click();
  await page.getByRole('textbox',{name:'匹配查询文本',exact:true}).fill('穿黑色衣服的人');await page.getByRole('button',{name:'检索并标注',exact:true}).click();
  await page.getByRole('radiogroup',{name:'判定 '+a1,exact:true}).getByRole('radio',{name:'正确',exact:true}).click();
  await page.getByRole('radiogroup',{name:'判定 '+a1,exact:true}).getByRole('radio',{name:'正确',exact:true,checked:true}).waitFor();
  await page.getByRole('radiogroup',{name:'判定 '+a2,exact:true}).getByRole('radio',{name:'错误',exact:true}).click();
  await page.getByRole('radiogroup',{name:'判定 '+a2,exact:true}).getByRole('radio',{name:'错误',exact:true,checked:true}).waitFor();
  await page.getByRole('radiogroup',{name:'匹配标注视图'}).getByRole('radio',{name:'分类视图',exact:true}).check();await page.getByText('黑衣',{exact:true}).waitFor();
  if(query.hits[0].judgment!=='correct'||query.hits[1].judgment!=='incorrect')throw Error('Judgments lost');
  await page.screenshot({path:'output/playwright/multimodal-vector-desktop.png'});checks.push('Text query, independent positive/negative judgments, shared category/description views');
  await page.getByRole('link',{name:'返回项目管理',exact:true}).click();
  await page.getByRole('combobox',{name:'处理方式',exact:true}).click();await page.locator('.ant-select-dropdown:visible').getByText('检测切图',{exact:true}).click();
  await page.getByRole('combobox',{name:'处理模型',exact:true}).click();
  for(const name of ['YOLO','RF-DETR','SAM3'])await page.locator('.ant-select-dropdown:visible').getByText(name+' · r1',{exact:true}).waitFor();
  await page.locator('.ant-select-dropdown:visible').getByText('RF-DETR · r1',{exact:true}).click();
  await page.getByRole('spinbutton',{name:'裁剪每侧外扩百分比'}).fill('35');
  await page.getByRole('row').filter({hasText:'person-one.png'}).getByRole('checkbox').check();await page.getByRole('button',{name:/检测切图 · 已选 1 张/}).click();
  if(job.model_id!=='model-1'||job.expansion!==.35||!job.crops_only)throw Error('Crop selection/expansion lost');checks.push('All enabled detectors selectable; RF-DETR crop with 35 percent per-side expansion');
  await page.getByRole('button',{name:'开始标注',exact:true}).click();
  await page.waitForURL('**/annotate/project/'+ids.vector);
  await page.setViewportSize({width:390,height:844});
  await page.waitForFunction(()=>document.documentElement.scrollWidth<=innerWidth);
  if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth))throw Error('Mobile overflow');
  await page.screenshot({path:'output/playwright/multimodal-vector-mobile.png'});checks.push('390px layout without page overflow');
  await page.setViewportSize({width:1440,height:1000});
  await page.getByRole('button',{name:'导出标注数据',exact:true}).click();const modal=page.getByRole('dialog',{name:'导出训练标注'});
  for(const label of ['向量 InfoNCE','向量 Contrastive','向量 Online Contrastive','向量 Cosine Similarity','大模型 SFT · messages + images']){
    await modal.getByRole('combobox',{name:'训练数据格式',exact:true}).click();await page.locator('.ant-select-dropdown:visible').getByText(label,{exact:true}).click();
  }
  await modal.getByRole('textbox',{name:'SFT训练指令',exact:true}).fill('描述<image>中的口罩');
  await modal.getByRole('combobox',{name:'训练数据来源',exact:true}).click();await page.locator('.ant-select-dropdown:visible').getByText('分类标签配对',{exact:true}).click();
  exportFailure='当前所选分类标签暂无审核通过标注';await modal.getByRole('button',{name:'下载 ZIP',exact:true}).click();await modal.getByText('当前所选分类标签暂无审核通过标注',{exact:true}).waitFor();
  if(exports.at(-1).dataset_format!=='swift_sft'||exports.at(-1).sample_source!=='labels'||exports.at(-1).instruction!=='描述<image>中的口罩')throw Error('SFT export settings lost');
  await modal.getByRole('combobox',{name:'训练数据格式',exact:true}).click();await page.locator('.ant-select-dropdown:visible').getByText('向量 InfoNCE',{exact:true}).click();
  await modal.getByRole('combobox',{name:'训练数据来源',exact:true}).click();await page.locator('.ant-select-dropdown:visible').getByText('检索匹配判定（正确 / 错误）',{exact:true}).click();
  exportFailure='InfoNCE需要至少一个人工确认的正样本';await modal.getByRole('button',{name:'下载 ZIP',exact:true}).click();await modal.getByText('InfoNCE需要至少一个人工确认的正样本',{exact:true}).waitFor();
  await page.setViewportSize({width:390,height:844});await page.waitForFunction(()=>document.documentElement.scrollWidth<=innerWidth);await page.screenshot({path:'output/playwright/swift-export-mobile.png'});await page.setViewportSize({width:1440,height:1000});
  await modal.getByRole('checkbox',{name:'包含待审核的描述和分类标注',exact:true}).check();
  checks.push('Swift SFT, all vector loss formats, explicit source, editable instruction and server error retained in export modal');
  const downloading=page.waitForEvent('download');await modal.getByRole('button',{name:'下载 ZIP',exact:true}).click();const download=await downloading;
  if(!download.suggestedFilename().endsWith('.zip')||exports.at(-1).dataset_format!=='infonce'||exports.at(-1).sample_source!=='matches'||exports.at(-1).approved_only!=='false')throw Error('Export options or download missing');checks.push('Browser ZIP export with explicit pending-annotation opt-in');
  await modal.waitFor({state:'hidden'});
  await page.getByRole('radiogroup',{name:'标注方式',exact:true}).getByText('以图搜图标注',{exact:true}).click();
  await page.getByRole('combobox',{name:'检索向量模型',exact:true}).click();await page.locator('.ant-select-dropdown:visible').getByText('WEMM · r1',{exact:true}).click();
  await page.getByLabel('上传匹配查询图片',{exact:true}).setInputFiles({name:'query.png',mimeType:'image/png',buffer:png});
  await page.getByRole('button',{name:'检索并标注',exact:true}).click();
  if(query.query.image_id!==a1)throw Error('Image query not submitted');checks.push('Uploaded image can be used directly as a retrieval query');
  await page.goto('http://localhost:3000/projects');await page.getByRole('button',{name:/新建项目/}).click();
  const projectModal=page.getByRole('dialog',{name:'新建项目',exact:true});
  await projectModal.getByRole('textbox',{name:/项目名称/}).fill('向量分类项目');
  await projectModal.getByRole('combobox',{name:/任务类型/}).click();
  await page.locator('.ant-select-dropdown:visible').getByText('向量匹配标注',{exact:true}).click();
  await projectModal.getByRole('combobox',{name:'分类方式',exact:true}).click();
  await page.locator('.ant-select-dropdown:visible').getByText('多标签分类',{exact:true}).click();
  await projectModal.getByRole('button',{name:/^确\s*定$/}).click();
  await projectModal.waitFor({state:'hidden'});
  const created=writes.find(w=>w.path==='/api/projects'&&w.method==='POST')?.input;
  if(created?.task_type!=='vector'||created?.label_schema.classification_mode!=='multi')throw Error('Project configuration lost');
  checks.push('New project supports vector type, optional categories and per-project multi-label mode');
  await page.getByRole('button',{name:'开始标注',exact:true}).click();await page.waitForURL('**/annotate/project/'+ids.vector);
  await page.getByRole('heading',{name:'向量标注工作台',exact:true}).waitFor();
  reads.length=0;await page.reload();await page.getByText('图片描述标注',{exact:true}).last().waitFor();await page.getByRole('link',{name:'编辑图片描述',exact:true}).first().waitFor();
  if(reads.some(path=>path.includes('/matches')||path==='/api/model-services'))throw Error('Plain description must not load vector models or queries');
  checks.push('Plain image description opens without embeddings, Milvus or matching requests');
  checks.push('Vector project image entry and direct workspace refresh use the dedicated annotation route');
  await page.evaluate(({project,asset})=>localStorage.setItem('annotation-progress:1:1:'+project,JSON.stringify({assetId:asset,name:'previous.png'})),{project:ids.vector,asset:a1});
  await page.goto('http://localhost:3000/annotations?project_id='+ids.vector);
  await page.getByRole('button',{name:'继续标注',exact:true}).click();await page.waitForURL('**/annotate/project/'+ids.vector);
  await page.getByRole('heading',{name:'向量标注工作台',exact:true}).waitFor();
  checks.push('Existing image progress resumes into vector annotation rather than the single-image editor');
  await page.goto('http://localhost:3000/annotate/project/'+ids.classification);
  await page.getByText('此项目不属于向量标注项目',{exact:true}).waitFor();
  if(await page.getByRole('combobox',{name:'检索向量模型',exact:true}).count())throw Error('Non-vector project opened vector annotation');
  checks.push('Non-vector project cannot mount matching controls');
  await page.goto('http://localhost:3000/annotate/project/'+ids.vector);
  await page.getByRole('combobox',{name:'项目标注范围',exact:true}).click();await page.locator('.ant-select-dropdown:visible').getByText('仅标注裁剪图',{exact:true}).click();
  await page.getByRole('row').filter({hasText:'person-one.png'}).waitFor({state:'hidden'});
  await page.getByRole('link',{name:'编辑图片描述',exact:true}).first().waitFor();
  if(await page.getByRole('link',{name:'编辑图片描述',exact:true}).count()!==1)throw Error('Description queue must contain only crops');
  await page.getByRole('link',{name:'项目图片',exact:true}).click();
  await page.getByRole('combobox',{name:'项目图片来源',exact:true}).click();await page.locator('.ant-select-dropdown:visible').getByText('仅裁剪图',{exact:true}).click();
  await page.getByRole('row').filter({hasText:'person-two.png'}).waitFor();
  if(await page.getByRole('row').filter({hasText:'person-one.png'}).count())throw Error('Project source filter leaked originals');
  await page.setViewportSize({width:390,height:844});await page.waitForFunction(()=>document.documentElement.scrollWidth<=innerWidth);
  checks.push('Saved crop-only scope drives description queue; project list filters private crops on mobile');
  if(errors.length)throw Error(errors.join('\n'));
  return {checks,writes:writes.length,pageErrors:errors,scope:'All APIs intercepted; no business data changed'};
}
