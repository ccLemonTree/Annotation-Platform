async page=>{
  await page.unrouteAll({behavior:'ignoreErrors'});page.setDefaultTimeout(8000);
  const base='http://127.0.0.1:3000',pid='11111111-1111-4111-8111-111111111111',aid='aaaaaaaa-aaaa-4aaa-8aaa-000000000001';
  const defaults={caption:'默认图片描述',review:'默认审核',match_review:'默认匹配',training_analysis:'默认训练',scene:'默认场景',attributes:'仅分析主体的可见属性，不根据背景猜测。',detection_preannotation:'按范例定义检测全部可见目标。'};
  const models=[{id:'22222222-2222-4222-8222-222222222222',name:'Qwen',kind:'caption',protocol:'openai_chat',enabled:true,revision:4,chat_prompts:defaults},
    {id:'33333333-3333-4333-8333-333333333333',name:'Grounding',kind:'detection',protocol:'model_service',endpoint:'/detect/grounding',enabled:true,revision:2,labels:['person']},
    {id:'44444444-4444-4444-8444-444444444444',name:'SAM3',kind:'detection',protocol:'model_service',endpoint:'/detect/sam3',enabled:true,revision:1,labels:['person']}];
  const project={id:pid,project_name:'弹窗提示词验收',task_type:'caption',label_schema:{object_attributes:{target_label:'car',target_labels:['car'],fields:[{key:'color',name:'颜色',options:['红','蓝'],instruction:'按可见颜色'}]}}};
  const jobs=[],writes=[],errors=[],headers={'X-Asset-Crop-Depth-Filter':'1','X-Asset-Crop-Batch-Filter':'1'};
  const check=(ok,message)=>{if(!ok)throw Error(message);};page.on('pageerror',e=>errors.push(e.message));
  await page.addInitScript(()=>localStorage.setItem('token','isolated-prompt-dialogs'));
  await page.route(base+'/api/**',route=>{
    const req=route.request(),path=new URL(req.url()).pathname,json=(value,status=200)=>route.fulfill({json:value,headers,status});
    if(path==='/api/auth/me')return json({id:1,role:'admin',username:'管理员'});
    if(path==='/api/projects/'+pid)return json(project);
    if(path==='/api/model-services')return json(models);
    if(path==='/api/model-services/prompt-defaults')return json(defaults);
    if(path==='/api/assets')return json({items:[{id:aid,project_id:pid,original_name:'car.png',source_type:'model_crop',width:64,height:48,device_info:{crop:{depth:1}}}],total:1,page:1,page_size:20});
    if(path==='/api/assets/selection')return json({asset_ids:[aid],total:1});
    if(path.endsWith('/content'))return route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="64" height="48"/>'});
    if(path.endsWith('/work/jobs')&&req.method()==='POST'){const body=req.postDataJSON();writes.push(body);const job={id:'job-'+jobs.length,operation:body.operation,model_id:body.model_id,revision:1,total:1,counts:{pending:1}};jobs.unshift(job);return json(job,201);}
    if(path.endsWith('/work/jobs'))return json(jobs);
    if(/\/work\/jobs\/job-\d+$/.test(path))return json({...jobs.find(row=>path.endsWith(row.id)),page:1,items:[]});
    if(req.method()!=='GET')throw Error('Unexpected write '+path);return json([]);
  });
  const choose=async(label,text)=>{await page.getByRole('combobox',{name:label,exact:true}).click();await page.locator('.ant-select-dropdown:visible .ant-select-item-option-content').getByText(text,{exact:true}).click();await page.waitForFunction(({label,text})=>document.querySelector(`[aria-label="${label}"]`)?.closest('.ant-select')?.textContent.includes(text),{label,text});};
  const mode=async text=>{await page.getByRole('tab',{name:'处理图片 · 切图 / 质检 / 描述 / 索引',exact:true}).click();await choose('处理方式',text);};
  const editor=async(button,title)=>{await page.getByRole('button',{name:button,exact:true}).click();const modal=page.getByRole('dialog',{name:title,exact:true});await modal.waitFor();return modal;};
  const submit=async()=>{const n=writes.length;await page.getByRole('button',{name:'选择当前筛选全部图片',exact:true}).click();await page.getByRole('button',{name:/ · 已选 1 张$/}).click();await page.getByText(/已排队 1 张图片/).waitFor();check(writes.length===n+1,'Wrong submission count');return writes.at(-1);};
  await page.setViewportSize({width:1440,height:1050});await page.goto(base+'/projects/'+pid+'/prepare?operation=quality');
  let modal=await editor('编辑质检提示词','图片质量检查提示词');let field=modal.getByRole('textbox',{name:'图片质量要求',exact:true});const original=await field.inputValue();
  await field.fill('取消的修改');await modal.getByRole('button',{name:'取消',exact:true}).click();
  modal=await editor('编辑质检提示词','图片质量检查提示词');field=modal.getByRole('textbox',{name:'图片质量要求',exact:true});check(await field.inputValue()===original,'Cancel applied draft');
  await field.fill('');check(await modal.getByRole('button',{name:'应用提示词',exact:true}).isDisabled(),'Required empty quality prompt applied');
  await modal.getByRole('button',{name:'恢复默认提示词',exact:true}).click();check(await field.inputValue()===original,'Restore does not restore default');
  await modal.getByRole('button',{name:'应用提示词',exact:true}).click();check(writes.length===0,'Applying prompt started a job');
  await mode('大模型预标注属性');await choose('处理模型','Qwen · r4');
  modal=await editor('编辑属性分析提示词','属性分析提示词');field=modal.getByRole('textbox',{name:'属性分析核心提示词',exact:true});check(await field.inputValue()===defaults.attributes,'Actual attribute core not shown');
  await field.fill('按主体可见颜色判断，模糊时无法判断。');await modal.getByRole('button',{name:'应用提示词',exact:true}).click();
  modal=await editor('补充属性分析要求','属性分析补充要求');await modal.getByRole('textbox').fill('仅查看前景车辆。');await modal.getByRole('button',{name:'应用提示词',exact:true}).click();
  let body=await submit();check(body.attribute_prompt==='按主体可见颜色判断，模糊时无法判断。'&&body.prompt==='仅查看前景车辆。','Attribute core and supplement not separate');
  await mode('检测切图');await choose('处理模型','SAM3 · r1');modal=await editor('编辑检测提示词','检测目标与提示词');
  check((await modal.innerText()).includes('目标类别'),'Fixed detector must explain target labels');await modal.getByRole('textbox').fill('person\nface');await modal.getByRole('button',{name:'应用提示词',exact:true}).click();
  body=await submit();check(body.labels.join(',')==='person,face'&&!body.prompt,'SAM3 labels not sent');
  await page.getByRole('tab',{name:'处理图片 · 切图 / 质检 / 描述 / 索引',exact:true}).click();await choose('处理模型','Grounding · r2');modal=await editor('编辑检测提示词','检测目标与提示词');
  await modal.getByRole('textbox').fill('face; hand');await modal.getByRole('button',{name:'应用提示词',exact:true}).click();body=await submit();check(body.prompt==='face; hand'&&!body.labels.length,'Switching detector leaked labels that filter the new prompt');
  await mode('检测切图并预标注属性');await choose('处理模型','Grounding · r2');await choose('属性分析模型','Qwen');
  modal=await editor('编辑检测提示词','检测目标与提示词');await modal.getByRole('textbox').fill('car');await modal.getByRole('button',{name:'应用提示词',exact:true}).click();
  body=await submit();check(body.detection_prompt==='car'&&body.attribute_prompt===defaults.attributes&&!body.prompt,'Combined phases prompts crossed');
  await mode('图片质量检查');check(!await page.locator('textarea:visible').count(),'Prompt fields still occupy main page');
  await page.screenshot({path:'output/playwright/prompt-dialogs-prepare.png',fullPage:true,animations:'disabled'});
  modal=await editor('编辑质检提示词','图片质量检查提示词');await page.setViewportSize({width:390,height:844});await modal.screenshot({path:'output/playwright/prompt-dialogs-mobile.png',animations:'disabled'});
  check(errors.length===0,errors.join('\n'));return {checks:13,taskSubmissions:writes.length,modelConfigurationWrites:0,errors};
}
