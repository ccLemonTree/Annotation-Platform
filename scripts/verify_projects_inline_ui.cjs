async page=>{
  page.setDefaultTimeout(12000);
  const first={id:'11111111-1111-4111-8111-111111111111',project_name:'工厂检测项目',task_type:'detection',status:'active',created_at:'2026-11-02T00:00:00Z',label_schema:{labels:['人']},total_assets:2,annotated_count:1,reviewed_count:0};
  const second={...first,id:'22222222-2222-4222-8222-222222222222',project_name:'仓库检测项目'};
  const caption={...first,id:'33333333-3333-4333-8333-333333333333',project_name:'图片描述项目',task_type:'caption'};
  const projects=[first,second,caption],writes=[],reads=[],unexpected=[];let role='admin';
  const model={enabled:true,key:'fixture',base_url:'http://fixture.invalid',endpoint:'/detect',model_name:'fixture',timeout_seconds:30,confidence:.3,iou:.5,labels:['人'],label_mapping:{},revision:1};
  const models=[{...model,id:'detector-fixture',name:'隔离检测模型',kind:'detection',protocol:'model_service'},{...model,id:'reviewer-fixture',name:'隔离视觉审核模型',kind:'caption',protocol:'openai_chat'}];
  let jobs=projects.slice(0,2).flatMap(project=>[
    {id:`running-${project.id}`,project_id:project.id,kind:'preannotate',status:'running',total:2,processed:1,approved_count:0,needs_review_count:1,failed_count:0,config:{},created_at:'2026-11-02T00:00:00Z'},
    {id:`failed-${project.id}`,project_id:project.id,kind:'review',status:'failed',total:1,processed:1,approved_count:0,needs_review_count:0,failed_count:1,config:{},created_at:'2026-11-02T00:00:00Z',error:'隔离测试错误'},
  ]);
  const item={id:'item-fixture',job_id:jobs[0].id,asset_id:'asset-fixture',annotation_id:'annotation-fixture',annotation_version:1,status:'needs_review',reason:'需管理员核对',evidence:{},result:{boxes:[]},project_id:first.id,project_name:first.project_name,asset_name:'factory.jpg',job_kind:'preannotate'};
  const uploadJobs={},api=/^http:\/\/localhost:3000\/api\//;
  await page.addInitScript(()=>{if(location.protocol==='http:')localStorage.setItem('token','isolated-inline-token');});
  const paginate=(items)=>({items,total:items.length,page:1,page_size:20});
  await page.route(api,async route=>{
    const request=route.request(),url=new URL(request.url()),path=url.pathname,method=request.method();
    if(method==='GET')reads.push(url.href);else writes.push({path,method,body:request.postDataJSON()});
    let data;
    if(path==='/api/auth/me')data={id:1,username:'fixture',role,tenant_id:1,is_active:true};
    else if(path==='/api/projects')data=paginate(projects);
    else if(path.startsWith('/api/projects/'))data=projects.find(project=>path.includes(project.id))||{};
    else if(path==='/api/model-services')data=models;
    else if(path==='/api/images/collections')data=[{id:'collection-fixture',name:'项目上传集',image_count:5,ingest_config:{min_long_edge:0,classification_enabled:false,scenes:[]}}];
    else if(path==='/api/images/ingest-settings')data={min_long_edge:0,classification_enabled:false};
    else if(path==='/api/image-uploads/config')data={chunk_size:1024,max_file_size:100000,max_image_size:100000,max_expanded_size:100000,max_entries:100,retention_days:1};
    else if(path==='/api/image-uploads'&&method==='GET')data=[];
    else if(path==='/api/image-uploads'&&method==='POST'){
      const filename=request.postDataJSON().filename,id=filename==='caption.png'?'upload-caption':'upload-detection';
      data=uploadJobs[id]={id,filename,collection_id:'collection-fixture',status:filename==='detection.png'?'pending':'completed',cursor:0,size:6,uploaded_parts:[],uploaded_bytes:6,min_long_edge:0,result:{imported:0,duplicates:1,filtered:0,invalid:0,ignored:0,errors:[]}};
    }else if(path==='/api/image-uploads/upload-detection')data={...uploadJobs['upload-detection'],status:'completed'};
    else if(/^\/api\/image-uploads\/[^/]+\/images$/.test(path))data={image_ids:[path.includes('upload-caption')?'image-caption':'image-detection'],total:1,page:1,page_size:1000};
    else if(path==='/api/images/associate')data={added_count:1,skipped_count:0,asset_ids:[],annotated:0,conflicts:0};
    else if(path==='/api/automation/jobs'&&method==='GET'){
      const id=url.searchParams.get('project_id');if(!id){unexpected.push('inline jobs missing project_id');return route.fulfill({status:400,json:{detail:'项目缺失'}});}
      data=paginate(jobs.filter(job=>job.project_id===id));
    }else if(path==='/api/automation/jobs'&&method==='POST'){
      const body=request.postDataJSON();data={...jobs[0],...body,id:`created-${writes.length}`,status:'running'};jobs=[data,...jobs];
    }else if(/^\/api\/automation\/jobs\/[^/]+\/items$/.test(path))data=paginate([item]);
    else if(/^\/api\/automation\/jobs\/[^/]+\/(cancel|retry)$/.test(path)){
      const id=path.split('/')[4];jobs=jobs.map(job=>job.id===id?{...job,status:path.endsWith('/cancel')?'canceled':'pending'}:job);data=jobs.find(job=>job.id===id);
    }else if(path==='/api/automation/review-queue')data=paginate(url.searchParams.get('project_id')===first.id?[item]:[]);
    else if(path==='/api/annotations/annotation-fixture')data={id:item.annotation_id,asset_id:item.asset_id,version:1,is_latest:true,annotation_source:'manual',annotation_data:{boxes:[]}};
    else if(path==='/api/assets/asset-fixture')data={id:item.asset_id,project_id:first.id,original_name:'factory.jpg',width:10,height:10};
    else if(path.endsWith('/content'))return route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/>'});
    else {unexpected.push(method+' '+path);data=[];}
    await route.fulfill({json:data});
  });
  const stay=()=>{if(new URL(page.url()).pathname!=='/projects')throw Error('行内操作离开了项目管理');};
  const row=name=>page.getByRole('article',{name,exact:true});
  const choose=async(name,text)=>{await page.getByRole('combobox',{name,exact:true}).click();await page.locator('.ant-select-dropdown:visible .ant-select-item-option-content').getByText(text,{exact:true}).click();};
  const upload=async(project,filename,imageId)=>{
    await row(project.project_name).getByRole('button',{name:/上传图片$/}).click();
    const modal=page.getByRole('dialog',{name:`上传到项目 ${project.project_name}`});await modal.waitFor();stay();
    await modal.getByLabel('上传图片集名称',{exact:true}).fill('项目上传集');
    await modal.getByLabel('上传图片或压缩包',{exact:true}).setInputFiles({name:filename,mimeType:'image/png',buffer:Buffer.from('fixture')});
    const completed=page.waitForResponse(response=>response.url().endsWith('/api/images/associate')&&response.request().postDataJSON().project_id===project.id);
    await modal.getByRole('button',{name:'开始 / 继续上传',exact:true}).click();
    if(filename==='detection.png'){
      await modal.getByText(/后台入库，已处理/).waitFor();await modal.getByRole('button',{name:'关闭',exact:true}).click();await modal.waitFor({state:'hidden'});
      await completed;
    }else{await completed;await modal.getByText(new RegExp(`已关联“${project.project_name}”新增 1 张`)).waitFor();}
    stay();
    const association=writes.filter(write=>write.path==='/api/images/associate').at(-1);
    if(association.body.project_id!==project.id||JSON.stringify(association.body.image_ids)!==JSON.stringify([imageId]))throw Error('行内上传关联了错误项目或整图片集');
    if(filename!=='detection.png'){await modal.getByRole('button',{name:'关闭',exact:true}).click();await modal.waitFor({state:'hidden'});}
  };
  try{
    await page.goto('http://localhost:3000/projects');
    await upload(first,'detection.png','image-detection');
    await upload(caption,'caption.png','image-caption');
    if(await row(caption.project_name).getByRole('button',{name:/预标注与AI审核$/}).count())throw Error('非检测项目出现检测批量入口');
    await row(first.project_name).getByRole('button',{name:/预标注与AI审核$/}).click();
    const drawer=page.getByRole('dialog').filter({has:page.getByRole('button',{name:'创建任务',exact:true})});await drawer.waitFor();stay();
    if(await drawer.getByRole('combobox',{name:'筛选项目'}).count())throw Error('内嵌模式仍允许切换项目');
    for(const kind of ['preannotate','review']){
      await drawer.getByRole('button',{name:'创建任务',exact:true}).click();
      const form=page.getByRole('dialog',{name:'创建预标注 / AI审核任务'});await form.waitFor();
      const projectField=form.getByRole('combobox',{name:'任务项目'});
      if(await projectField.count()&&!await projectField.isDisabled())throw Error('内嵌任务项目未锁定');
      if(kind==='review')await choose('任务类型','AI审核（已有标注）');
      await form.getByRole('button',{name:'提交任务',exact:true}).click();await form.waitFor({state:'hidden'});stay();
      const created=writes.filter(write=>write.path==='/api/automation/jobs'&&write.method==='POST').at(-1);
      if(created.body.project_id!==first.id||created.body.kind!==kind)throw Error('行内任务创建项目或类型错绑');
    }
    const running=drawer.getByRole('row').filter({hasText:`running-${first.id}`});
    await running.getByRole('button',{name:'查看明细',exact:true}).click();
    await page.getByRole('dialog',{name:`任务明细 · running-${first.id}`}).waitFor();stay();
    await page.getByRole('button',{name:'关闭明细',exact:true}).click();
    await running.getByRole('button',{name:'取消任务',exact:true}).click();await page.getByRole('button',{name:'确认取消',exact:true}).click();
    await drawer.getByRole('row').filter({hasText:`failed-${first.id}`}).getByRole('button',{name:'重试任务',exact:true}).click();stay();
    await drawer.getByRole('button',{name:'预览审核',exact:true}).click();await page.getByRole('dialog',{name:'管理员审核预览'}).waitFor();stay();
    await page.getByRole('button',{name:'关闭预览',exact:true}).click();
    await page.getByRole('dialog',{name:'管理员审核预览'}).waitFor({state:'hidden'});
    await page.locator('.ant-spin-spinning').waitFor({state:'hidden'});
    await drawer.locator('.ant-drawer-body').evaluate(element=>{element.scrollTop=0;});
    await page.screenshot({path:'output/playwright/projects-inline-desktop.png',fullPage:true,animations:'disabled'});
    await page.setViewportSize({width:390,height:844});
    await page.waitForFunction(()=>getComputedStyle(document.querySelector('.platform-main')).marginLeft==='0px');
    if(await page.locator('html').evaluate(element=>element.scrollWidth>element.clientWidth+1))throw Error('项目行内操作手机端溢出');
    await page.screenshot({path:'output/playwright/projects-inline-mobile.png',fullPage:true,animations:'disabled'});
    await page.setViewportSize({width:1440,height:1000});
    await page.locator('.ant-drawer-close').click();await drawer.waitFor({state:'hidden'});
    await row(second.project_name).getByRole('button',{name:/预标注与AI审核$/}).click();await drawer.waitFor();
    await drawer.getByRole('row').filter({hasText:`running-${second.id}`}).waitFor();
    if(await drawer.getByRole('row').filter({hasText:`running-${first.id}`}).count())throw Error('切换项目仍显示上一项目任务');
    await drawer.getByRole('button',{name:'创建任务',exact:true}).click();
    const secondForm=page.getByRole('dialog',{name:'创建预标注 / AI审核任务'});await secondForm.getByRole('button',{name:'提交任务',exact:true}).click();await secondForm.waitFor({state:'hidden'});stay();
    if(writes.filter(write=>write.path==='/api/automation/jobs'&&write.method==='POST').at(-1).body.project_id!==second.id)throw Error('切换项目后新任务仍错绑上一项目');
    if(unexpected.length)throw Error('未覆盖隔离请求: '+unexpected.join(';'));
    return {checks:['检测与描述项目行内精确批次上传','检测批量接口类型限制','预标注/AI审核锁定当前项目','项目进度/明细/取消/重试','内嵌管理员审核预览','始终停留/projects','390px布局'],scope:'全部根/api拦截，无真实上传或批量任务',writes};
  }catch(error){await page.screenshot({path:'output/playwright/projects-inline-failure.png',fullPage:true,animations:'disabled'});throw error;}
  finally{await page.goto('about:blank');await page.unroute(api);await page.setViewportSize({width:1440,height:1000});}
}
