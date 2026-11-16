async page=>{
  const base='http://127.0.0.1:3017',id='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',model='cccccccc-cccc-4ccc-8ccc-cccccccccccc';
  const ids=Array.from({length:5002},(_,index)=>`00000000-0000-4000-8000-${String(index+1).padStart(12,'0')}`);
  const project={id,project_name:'属性控件回归',task_type:'caption',label_schema:{object_attributes:{target_label:'car',fields:[{key:'color',name:'颜色',instruction:'',options:[]}]}}};
  const errors=[],writes=[],selections=[];let failSelection=false,selectionDelay=0,failBatch=0;
  await page.addInitScript(()=>{localStorage.setItem('token','isolated-controls');Object.defineProperty(crypto,'randomUUID',{value:undefined,configurable:true});});
  page.on('pageerror',error=>errors.push(error.message));
  await page.route(/^http:\/\/127\.0\.0\.1:3017\/api(?:\/|\?|$)/,async route=>{
    const request=route.request(),url=new URL(request.url()),path=url.pathname;
    if(request.method()!=='GET'){
      const body=request.postDataJSON();
      if(path===`/api/projects/${id}`){project.label_schema=body.label_schema;return route.fulfill({json:project});}
      if(path.endsWith('/work/jobs')){
        writes.push(body);
        if(failBatch===writes.length)return route.fulfill({status:409,json:{detail:'模型已变更'}});
        return route.fulfill({json:{id:'batch-'+writes.length,operation:body.operation,total:body.asset_ids.length,counts:{pending:body.asset_ids.length},model_id:model,revision:1}});
      }
      throw Error('Unexpected write '+path);
    }
    let data=[];
    if(path==='/api/auth/me')data={id:1,username:'fixture',role:'admin',tenant_id:1};
    else if(path===`/api/projects/${id}`)data=project;
    else if(path==='/api/model-services')data=[{id:model,kind:'detection',protocol:'model_service',name:'检测模型',revision:1,labels:['car']},{id:model,kind:'caption',protocol:'openai_chat',name:'属性模型',revision:1}];
    else if(path==='/api/assets/selection'){
      selections.push(Object.fromEntries(url.searchParams));
      if(selectionDelay)await new Promise(resolve=>setTimeout(resolve,selectionDelay));
      if(failSelection)return route.fulfill({status:500,json:{detail:'读取失败'}});
      data={asset_ids:url.searchParams.get('image_kind')==='crops'?[ids[0]]:ids,total:ids.length};
    }
    else if(path==='/api/assets'){
      const pageNumber=Number(url.searchParams.get('page')||1),crops=url.searchParams.get('image_kind')==='crops';
      const rows=crops?[ids[0]]:ids.slice((pageNumber-1)*20,pageNumber*20);
      data={items:rows.map((assetId,index)=>({id:assetId,project_id:id,original_name:'image-'+((pageNumber-1)*20+index+1)+'.png',source_type:crops?'model_crop':'upload'})),total:crops?1:ids.length};
    }
    else if(path.endsWith('/attributes'))data={items:[],total:0};
    else if(path.endsWith('/content'))return route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16"/>'});
    else if(path.includes('/work/jobs/batch-'))data={id:path.split('/').pop(),operation:'detect_attributes',total:1,counts:{pending:1},model_id:model,revision:1,page:1,items:[]};
    else if(['/api/model-services','/api/model-services/prompt-defaults','/api/attribute-templates'].includes(path)||path.endsWith('/work/jobs'))data=[];
    else {errors.push(`Unexpected API GET ${path}`);return route.fulfill({status:500,json:{detail:'Unexpected API'}});}
    return route.fulfill({headers:{'X-Asset-Crop-Depth-Filter':'1','X-Asset-Attribute-Filter':'1','X-Asset-Filename-Filter':'1'},json:data});
  });
  await page.goto(base+`/projects/${id}/attributes`);
  await page.getByRole('button',{name:'查看 / 编辑配置',exact:true}).click();
  await page.getByLabel('属性名称 1',{exact:true}).waitFor();
  await page.getByRole('button',{name:'添加属性',exact:true}).click();
  await page.getByLabel('属性名称 2',{exact:true}).waitFor({timeout:2500});
  await page.getByLabel('属性名称 2',{exact:true}).fill('品牌');
  await page.getByRole('button',{name:'添加属性',exact:true}).click();
  await page.getByLabel('属性名称 3',{exact:true}).fill('朝向');
  await page.getByRole('button',{name:'保存属性配置',exact:true}).click();
  await page.getByText('检测标签：car',{exact:true}).waitFor();
  const fields=project.label_schema.object_attributes.fields;
  if(fields.length!==3||new Set(fields.map(field=>field.key)).size!==3)throw Error('Attribute addition did not persist unique keys');
  await page.getByRole('button',{name:'选择当前筛选全部图片',exact:true}).click({timeout:2500});
  await page.getByRole('button',{name:/检测切图并预标注属性 · 已选 5002 张$/}).waitFor();
  if(selections.at(-1).scope!=='all'||selections.at(-1).image_kind!=='originals')throw Error('Selection scope differs from list');
  await page.getByRole('row').filter({hasText:'image-1.png'}).getByRole('checkbox').uncheck();
  await page.getByRole('button',{name:/检测切图并预标注属性 · 已选 5001 张$/}).waitFor();
  await page.getByRole('button',{name:'清空选择',exact:true}).click();
  await page.getByRole('button',{name:/检测切图并预标注属性 · 已选 0 张$/}).waitFor();
  await page.getByRole('combobox',{name:'处理方式',exact:true}).click();
  await page.getByText('大模型预标注属性',{exact:true}).click();
  await page.getByRole('button',{name:'选择当前筛选全部图片',exact:true}).click();
  await page.getByRole('button',{name:/大模型预标注属性 · 已选 1 张$/}).waitFor();
  if(selections.at(-1).scope!=='annotation'||selections.at(-1).image_kind!=='crops')throw Error('Crop selection scope lost');
  failSelection=true;
  await page.getByRole('button',{name:'选择当前筛选全部图片',exact:true}).click();
  await page.getByText('读取失败',{exact:true}).waitFor();
  if(!await page.getByRole('button',{name:/大模型预标注属性 · 已选 1 张$/}).count())throw Error('Selection failed and lost previous values');
  failSelection=false;
  await page.getByRole('combobox',{name:'处理方式',exact:true}).click();
  await page.getByText('检测切图并预标注属性',{exact:true}).click();
  selectionDelay=250;
  await page.getByRole('button',{name:'选择当前筛选全部图片',exact:true}).click();
  if(!await page.getByRole('combobox',{name:'处理方式',exact:true}).isDisabled())throw Error('Scope changed during selection fetch');
  await page.getByRole('button',{name:/检测切图并预标注属性 · 已选 5002 张$/}).waitFor();
  await page.getByRole('combobox',{name:'处理模型',exact:true}).click();await page.getByText('检测模型 · r1',{exact:true}).click();
  await page.getByRole('combobox',{name:'属性分析模型',exact:true}).click();await page.getByText('属性模型',{exact:true}).click();
  failBatch=2;
  await page.getByRole('button',{name:/检测切图并预标注属性 · 已选 5002 张$/}).click();
  await page.getByText(/已排队 5000 张/).waitFor();
  await page.getByRole('tab',{name:'处理图片 · 切图 / 质检 / 描述 / 索引',exact:true}).click();
  await page.getByRole('button',{name:/检测切图并预标注属性 · 已选 2 张$/}).waitFor();
  if(writes[0].asset_ids.length!==5000||writes[1].asset_ids.length!==2)throw Error('Work exceeded batch size');
  failBatch=0;
  await page.getByRole('button',{name:/检测切图并预标注属性 · 已选 2 张$/}).click();
  await page.getByRole('tab',{name:'后台处理队列',exact:true,selected:true}).waitFor();
  if(writes[2].asset_ids.join()!==ids.slice(5000).join())throw Error('Retry resubmitted successful batch');
  if(errors.length)throw Error(errors.join('\n'));
  return {passed:true,http_attribute_add:true,unique_attribute_keys:true,cross_page_selection:5002,scope_preserved:true,selection_error_preserves_selection:true,batch_size:5000,partial_failure_retry:true,errors};
}
