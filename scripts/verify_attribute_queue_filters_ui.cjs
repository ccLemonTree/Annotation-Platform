async page=>{
  const base='http://127.0.0.1:3017',id='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
  const schema={target_label:'car',fields:[{key:'orientation',name:'车身朝向',instruction:'可见朝向',options:['车头','车尾']},{key:'plate',name:'车牌',instruction:'清晰字符',options:[]}]};
  const project={id,project_name:'车辆属性',task_type:'caption',label_schema:{object_attributes:schema,annotation_scope:'crops'}};
  const rows=[
    {asset_id:'known',original_name:'known.png',attribute_schema:schema,attributes:{orientation:{value:'车尾',reason:'可见'},plate:{value:'TEST123',reason:'可见'}},review_status:'pending'},
    {asset_id:'unknown',original_name:'unknown.png',attribute_schema:schema,attributes:{orientation:{value:null,reason:'遮挡'},plate:{value:null,reason:'遮挡'}},review_status:'pending'},
    {asset_id:'missing',original_name:'missing.png',attribute_schema:null,attributes:{},review_status:null},
  ];
  const root={id:'root-batch',operation:'detect_attributes',model_id:'sam',revision:1,model_name:'SAM3',attribute_model_name:'Qwen3.8-27B',created_at:'2026-11-04T02:36:03Z',target_labels:['car'],attribute_fields:['车身朝向','车牌'],total:2602,counts:{completed:400,failed:1,running:1,pending:2200},
    pipeline:{detection:{total:2602,counts:{completed:400,failed:1,running:1,pending:2200}},attributes:{total:1726,counts:{pending:1726}},generated_targets:1726,child_batches:369,preserved_annotations:0},waiting_reason:'原图检测尚未结束，属性分析排队等待；当前队列先处理原图检测。',failure_reasons:[{operation:'detect_attributes',reason:'模型服务超时，请稍后重试',count:1}]};
  const errors=[],reads=[];
  await page.addInitScript(()=>localStorage.setItem('token','isolated-queue-filters'));
  page.on('pageerror',error=>errors.push(error.message));
  await page.route(base+'/api/**',async route=>{
    const request=route.request(),url=new URL(request.url()),path=url.pathname;
    if(request.method()!=='GET')throw Error('Unexpected write '+path);
    let data=[];
    if(path==='/api/auth/me')data={id:1,username:'fixture',role:'admin',tenant_id:1};
    else if(path===`/api/projects/${id}`)data=project;
    else if(path.endsWith('/work/jobs')){reads.push(url.href);data=url.searchParams.get('roots_only')==='true'?[root]:[{...root,id:'child-hidden',operation:'attributes',pipeline:undefined}];}
    else if(path.endsWith('/work/jobs/root-batch'))data={...root,counts:{completed:2601,failed:1},page:1,items:[{id:'failed-item',asset_id:'source',original_name:'original-road.jpg',status:'failed',error:'模型服务超时，请稍后重试'}]};
    else if(path.endsWith('/attributes')){
      reads.push(url.href);let items=rows;
      const state=url.searchParams.get('attribute_state'),key=url.searchParams.get('attribute_key'),value=url.searchParams.get('attribute_value');
      if(key&&state==='analyzed')items=items.filter(row=>row.attributes[key]);
      if(key&&state==='known')items=items.filter(row=>row.attributes[key]?.value!=null);
      if(key&&state==='unknown')items=items.filter(row=>row.attributes[key]&&row.attributes[key].value===null);
      if(key&&state==='missing')items=items.filter(row=>!row.attributes[key]);
      if(value)items=items.filter(row=>row.attributes[key]?.value===value);
      data={items,total:items.length};
    }
    else if(path==='/api/assets')data={items:[],total:0};
    else if(path.endsWith('/content'))return route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="32" height="24"/>'});
    return route.fulfill({json:data});
  });
  await page.goto(base+`/projects/${id}/attributes`);
  await page.getByRole('tab',{name:'后台处理队列',exact:true}).click();
  await page.getByText('阶段一：检测切图',{exact:true}).waitFor({timeout:2500});
  await page.getByText('阶段二：属性分析',{exact:true}).waitFor();
  if(!reads.some(url=>url.includes('roots_only=true')))throw Error('Automatic child jobs still flood queue');
  await page.getByText('SAM3',{exact:true}).waitFor();await page.getByText('Qwen3.8-27B',{exact:true}).waitFor();
  await page.getByText(root.waiting_reason,{exact:true}).waitFor();
  await page.getByRole('button',{name:'查看明细',exact:true}).click();
  await page.getByText('original-road.jpg',{exact:true}).waitFor();await page.getByText('模型服务超时，请稍后重试',{exact:true}).waitFor();
  if(await page.getByRole('button',{name:'取消未完成项',exact:true}).isDisabled())throw Error('Pending attribute stage cannot be cancelled after original detection finished');
  await page.screenshot({path:'output/playwright/attribute-work-pipeline-queue.png',fullPage:true});
  await page.getByRole('combobox',{name:'筛选属性',exact:true}).click();await page.getByText('车身朝向',{exact:true}).last().click();
  await page.getByText('共 2 条符合条件',{exact:true}).waitFor();
  if(await page.getByText('尚未分析',{exact:true}).count())throw Error('Attribute selection still includes unanalyzed crops by default');
  if(await page.getByText('车牌：TEST123',{exact:true}).count())throw Error('Selected field still renders every attribute');
  const valueInput=page.getByLabel('属性值筛选',{exact:true});await valueInput.fill('车尾');await valueInput.press('Escape');
  await page.getByText('共 1 条符合条件',{exact:true}).waitFor();await page.getByText('车身朝向：车尾',{exact:true}).waitFor();
  await valueInput.fill('');
  await page.getByRole('combobox',{name:'属性判断状态',exact:true}).click();await page.getByText('无法判断',{exact:true}).last().click();
  await page.getByText('车身朝向：无法判断（遮挡）',{exact:true}).waitFor();
  await page.getByRole('combobox',{name:'属性判断状态',exact:true}).click();await page.getByText('尚未分析此属性',{exact:true}).last().click();
  await page.getByText('车身朝向：尚未分析',{exact:true}).waitFor();
  await page.screenshot({path:'output/playwright/attribute-filter-pending.png',fullPage:true});
  if(errors.length)throw Error(errors.join('\n'));
  return {passed:true,grouped_pipeline_queue:true,two_stage_units:true,waiting_reason:true,filename_and_error_details:true,analyzed_default:true,known_value_filter:true,unknown_filter:true,missing_filter:true,selected_field_display:true,errors};
}
