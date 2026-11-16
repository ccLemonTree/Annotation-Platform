async page=>{
  await page.unrouteAll();await page.setViewportSize({width:1440,height:1000});
  const base='http://127.0.0.1:3017',projectId='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',assetId='bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb';
  const jevId='cccccccc-cccc-4ccc-8ccc-cccccccccccc',detectorId='dddddddd-dddd-4ddd-8ddd-dddddddddddd';
  const config={target_label:'person',fields:[{key:'hat',name:'帽子',instruction:'只看头部',options:['鸭舌帽','头盔','没戴帽子']}]};
  const probabilities={'鸭舌帽':.02,'头盔':.95,'没戴帽子':.03};
  const project={id:projectId,project_name:'JEV属性隔离验证',task_type:'caption',status:'active',label_schema:{object_attributes:config,annotation_scope:'all'}};
  const asset={id:assetId,project_id:projectId,original_name:'person.png',source_type:'model_crop',width:64,height:48};
  let annotation={id:'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee',asset_id:assetId,version:1,is_latest:true,review_status:'pending',annotation_source:'auto',
    annotation_data:{labels:[],description:'',attribute_schema:config,attributes:{hat:{value:'头盔',reason:'JEV最高概率候选，待人工核验',probabilities}}}};
  let models=[{id:jevId,key:'jev',name:'JEV-27B-VL',kind:'caption',protocol:'jev_decide',base_url:'http://model.test/v1',endpoint:'/decide',model_name:'',enabled:true,revision:1},
    {id:detectorId,key:'detector',name:'人员检测',kind:'detection',protocol:'model_service',enabled:true,revision:1,labels:['person']}];
  const writes=[],errors=[];
  await page.addInitScript(()=>localStorage.setItem('token','isolated-jev'));
  page.on('pageerror',error=>errors.push(error.message));
  page.on('console',message=>{if(message.type()==='error')errors.push(message.text());});
  await page.route(base+'/api/**',async route=>{
    const request=route.request(),url=new URL(request.url()),path=url.pathname;
    if(request.method()!=='GET'){
      const body=request.postData()?request.postDataJSON():undefined;writes.push({path,body});
      if(path.endsWith('/test'))return route.fulfill({json:{ok:true,detail:'JEV协议信息可访问'}});
      if(path==='/api/model-services'){const model={...body,id:'ffffffff-ffff-4fff-8fff-ffffffffffff',revision:1};models.push(model);return route.fulfill({json:model});}
      if(path.endsWith('/work/jobs'))return route.fulfill({json:{id:'job-jev',operation:body.operation,total:1,counts:{pending:1},model_id:jevId,revision:1}});
      if(path===`/api/annotations/${annotation.id}`){annotation={...annotation,id:'99999999-9999-4999-8999-999999999999',annotation_data:body.annotation_data,annotation_source:'manual',version:annotation.version+1};return route.fulfill({json:annotation});}
      if(path.endsWith('/review')){annotation={...annotation,review_status:body.approved?'approved':'rejected'};return route.fulfill({json:annotation});}
      throw Error('Unexpected write '+path);
    }
    if(path.endsWith('/content'))return route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="64" height="48"><rect width="64" height="48" fill="#eee"/></svg>'});
    if(path.endsWith('/attributes/export'))return route.fulfill({contentType:'application/zip',body:'isolated-archive'});
    let data=[];
    if(path==='/api/auth/me')data={id:1,role:'admin',username:'fixture',tenant_id:1};
    else if(path==='/api/model-services')data=models;
    else if(path.endsWith('/prompt-defaults'))data={caption:'描述',review:'审核',match_review:'匹配',training_analysis:'分析',scene:'场景'};
    else if(path===`/api/projects/${projectId}`)data=project;
    else if(path===`/api/assets/${assetId}`)data=asset;
    else if(path==='/api/assets')data={items:[asset],total:1};
    else if(path.endsWith('/neighbors'))data={previous_id:null,next_id:null,position:1,total:1,matched:true};
    else if(path==='/api/annotations')data={items:[annotation],total:1};
    else if(path.endsWith('/attributes'))data={items:[{...asset,asset_id:assetId,attribute_schema:config,attributes:annotation.annotation_data.attributes,review_status:annotation.review_status,annotation_source:annotation.annotation_source}],total:1};
    else if(path.endsWith('/work/jobs/job-jev'))data={id:'job-jev',operation:'attributes',total:1,counts:{pending:1},model_id:jevId,revision:1,page:1,items:[]};
    return route.fulfill({json:data});
  });
  await page.goto(base+'/models');
  await page.getByRole('button',{name:'添加模型',exact:true}).click();
  await page.getByLabel('KEY',{exact:true}).fill('jev-extra');
  await page.getByLabel('名称',{exact:true}).fill('JEV新增验证');
  await page.getByLabel('URL',{exact:true}).fill('http://model.test/v1');
  await page.getByRole('combobox',{name:'模型类型',exact:true}).click();
  await page.getByText('图像描述',{exact:true}).last().click();
  await page.getByRole('combobox',{name:'模型协议',exact:true}).click();
  await page.getByText('jev_decide · JEV属性选择',{exact:true}).click();
  if(await page.getByLabel('ENDPOINT',{exact:true}).inputValue()!=='/decide')throw Error('JEV endpoint not filled');
  await page.getByRole('button',{name:'保存模型',exact:true}).click();
  await page.getByRole('dialog',{name:'添加模型',exact:true}).waitFor({state:'hidden'});
  const saved=writes.find(row=>row.path==='/api/model-services')?.body;
  if(saved?.protocol!=='jev_decide'||saved.model_name!=='')throw Error('JEV config requires OpenAI model id');
  if(!await page.getByRole('row').filter({hasText:'JEV-27B-VL'}).getByRole('button',{name:'试运行',exact:true}).isDisabled())throw Error('Generic trial accepts choice model');

  await page.goto(base+`/projects/${projectId}/attributes`);
  await page.getByRole('combobox',{name:'属性分析模型',exact:true}).click();await page.getByText('JEV-27B-VL',{exact:true}).click();
  await page.getByText('JEV逐属性选择并保留全部候选概率',{exact:true}).waitFor();
  if(await page.getByRole('button',{name:'选择正确范例（可选）',exact:true}).count())throw Error('JEV incorrectly offers examples');
  await page.getByRole('combobox',{name:'处理方式',exact:true}).click();await page.getByText('大模型预标注属性',{exact:true}).click();
  await page.getByRole('combobox',{name:'处理模型',exact:true}).click();await page.getByText('JEV-27B-VL · r1',{exact:true}).click();
  await page.getByRole('row').filter({hasText:'person.png'}).first().getByRole('checkbox').check();
  await page.getByRole('button',{name:'大模型预标注属性 · 已选 1 张',exact:true}).click();
  const job=writes.find(row=>row.path.endsWith('/work/jobs'))?.body;
  if(job?.model_id!==jevId||job.example_asset_id)throw Error('JEV job payload incorrect');
  await page.getByText('查看模型原始候选概率',{exact:true}).click();
  await page.getByText('头盔：95.00%',{exact:true}).waitFor();
  await page.screenshot({path:'output/playwright/jev-results.png',fullPage:true});

  await page.goto(base+`/annotate/${assetId}?from=attributes`);
  await page.getByText('查看模型原始候选概率',{exact:true}).click();
  await page.getByText('鸭舌帽：2.00%',{exact:true}).waitFor();
  await page.getByRole('button',{name:'帽子选择没戴帽子',exact:true}).click();
  await page.getByText('说明 / 修正原因',{exact:true}).click();
  await page.getByLabel('帽子判断原因',{exact:true}).fill('人工放大核对');
  await page.getByRole('button',{name:'保存标注',exact:true}).click();
  await page.getByText('v2 · 待审核',{exact:true}).waitFor();
  if(JSON.stringify(annotation.annotation_data.attributes.hat.probabilities)!==JSON.stringify(probabilities)||annotation.annotation_data.attributes.hat.value!=='没戴帽子')throw Error('Manual correction lost probabilities');
  await page.getByRole('button',{name:'审核通过',exact:true}).click();
  await page.getByText('v2 · 已通过',{exact:true}).waitFor();
  await page.setViewportSize({width:390,height:844});
  await page.screenshot({path:'output/playwright/jev-manual-mobile.png',fullPage:true});
  if(errors.length)throw Error(errors.join('\n'));
  return {passed:true,model_configuration:true,jev_selection:true,unsupported_example_hidden:true,all_probabilities_visible:true,manual_correction_preserves_probabilities:true,review:true,errors};
}
