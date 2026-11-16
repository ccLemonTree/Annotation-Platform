async page=>{
  await page.unrouteAll({behavior:'ignoreErrors'});page.setDefaultTimeout(7000);
  const base='http://127.0.0.1:3000',projectId='11111111-1111-4111-8111-111111111111',assetId='aaaaaaaa-aaaa-4aaa-8aaa-000000000001';
  const defaultPrompt='逐项描述图片可见内容。'.repeat(240),models=[
    {id:'22222222-2222-4222-8222-222222222222',name:'Qwen',kind:'caption',protocol:'openai_chat',chat_prompts:{caption:defaultPrompt},enabled:true,revision:4},
    {id:'33333333-3333-4333-8333-333333333333',name:'Grounding',kind:'detection',protocol:'model_service',endpoint:'/detect/grounding',labels:['person'],enabled:true,revision:2},
    {id:'44444444-4444-4444-8444-444444444444',name:'SAM3',kind:'detection',protocol:'model_service',endpoint:'/detect/sam3',labels:['person'],enabled:true,revision:3},
    {id:'55555555-5555-4555-8555-555555555555',name:'JEV',kind:'caption',protocol:'jev_decide',enabled:true,revision:1},
    {id:'66666666-6666-4666-8666-666666666666',name:'Embedding',kind:'embedding',protocol:'openai_embedding',enabled:true,revision:1}
  ];
  const project={id:projectId,project_name:'页面提示词',task_type:'vector',status:'active',label_schema:{object_attributes:{target_label:'person',target_labels:['person'],fields:[{key:'color',name:'颜色',options:['红','蓝'],description:'仅看可见颜色'}]}}};
  const writes=[],errors=[],jobs=[];const check=(condition,message)=>{if(!condition)throw Error(message);};
  const headers={'X-Asset-Crop-Depth-Filter':'1','X-Asset-Crop-Batch-Filter':'1'};
  await page.addInitScript(()=>localStorage.setItem('token','isolated-inline-prompts'));page.on('pageerror',error=>errors.push(error.message));
  await page.route(base+'/api/**',async route=>{
    const req=route.request(),u=new URL(req.url()),p=u.pathname;
    if(p==='/api/auth/me')return route.fulfill({json:{id:1,tenant_id:1,username:'管理员',role:'admin'}});
    if(p==='/api/analytics/activity')return route.fulfill({json:{}});
    if(p==='/api/projects/'+projectId)return route.fulfill({json:project});
    if(p==='/api/model-services')return route.fulfill({json:models});
    if(p==='/api/assets/selection')return route.fulfill({headers,json:{asset_ids:[assetId],total:1}});
    if(p==='/api/assets')return route.fulfill({headers,json:{items:[{id:assetId,project_id:projectId,original_name:'person.png',source_type:'model_crop',width:64,height:48,device_info:{crop:{depth:1}}}],total:1,page:1,page_size:20}});
    if(p.endsWith('/content'))return route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="64" height="48"><rect width="64" height="48" fill="#314c74"/></svg>'});
    if(p.endsWith('/work/jobs')&&req.method()==='POST'){
      const body=req.postDataJSON();writes.push({path:p,method:req.method(),body});
      const job={id:'job-'+jobs.length,operation:body.operation,model_id:body.model_id,revision:4,total:1,counts:{pending:1}};jobs.unshift(job);return route.fulfill({status:201,json:job});
    }
    if(p.endsWith('/work/jobs'))return route.fulfill({json:jobs});
    if(/\/work\/jobs\/job-\d+$/.test(p))return route.fulfill({json:{...jobs.find(job=>p.endsWith(job.id)),page:1,items:[]}});
    if(req.method()!=='GET')throw Error('Unexpected write '+req.method()+' '+p);
    return route.fulfill({json:[]});
  });
  const choose=async(label,text)=>{await page.getByRole('combobox',{name:label,exact:true}).click();await page.locator('.ant-select-dropdown:visible .ant-select-item-option-content').getByText(text,{exact:true}).click();await page.waitForFunction(({label,text})=>document.querySelector(`[aria-label="${label}"]`)?.closest('.ant-select')?.textContent.includes(text),{label,text});};
  const operation=async text=>{if(await page.getByRole('tab',{name:'处理图片 · 切图 / 质检 / 描述 / 索引',exact:true}).count())await page.getByRole('tab',{name:'处理图片 · 切图 / 质检 / 描述 / 索引',exact:true}).click();await choose('处理方式',text);};
  const submit=async()=>{const before=writes.length;await page.getByRole('button',{name:'选择当前筛选全部图片',exact:true}).click();await page.getByRole('button',{name:/ · 已选 1 张$/}).click();await page.waitForFunction(()=>document.body.innerText.includes('已排队 1 张图片'));check(writes.length===before+1,'Submission count wrong');return writes.at(-1).body;};
  await page.setViewportSize({width:1480,height:1080});await page.goto(base+'/projects/'+projectId+'/prepare');
  await operation('生成描述');await choose('处理模型','Qwen · r4');
  await page.getByRole('button',{name:'编辑描述提示词',exact:true}).click();const description=page.getByRole('textbox',{name:'本批描述提示词',exact:true});await description.waitFor();
  check(await description.inputValue()===defaultPrompt,'Current page does not load full model default prompt');
  check(!await page.getByRole('link',{name:'配置模型 / 提示词',exact:true}).count(),'Prompt editor still routes through model management');
  await description.fill('只描述主体人物，说明遮挡，不猜测。');
  await page.getByRole('button',{name:'恢复默认提示词',exact:true}).click();check(await description.inputValue()===defaultPrompt,'Restore default changed model or lost text');
  await description.fill('本次描述提示词');await page.screenshot({path:'output/playwright/inline-description-prompt.png',fullPage:true,animations:'disabled'});
  await page.getByRole('button',{name:'应用提示词',exact:true}).click();let body=await submit();check(body.operation==='caption'&&body.prompt==='本次描述提示词','Description prompt not submitted');
  await operation('大模型预标注属性');await choose('处理模型','Qwen · r4');
  await page.getByRole('button',{name:'补充属性分析要求',exact:true}).click();const attributes=page.getByRole('textbox',{name:'本批属性分析补充要求',exact:true});await attributes.waitFor();check(await attributes.inputValue()==='','Description leaked into attributes');
  await attributes.fill('只判断可见衣服颜色，不用背景推断。');await page.getByRole('button',{name:'应用提示词',exact:true}).click();body=await submit();check(body.operation==='attributes'&&body.prompt==='只判断可见衣服颜色，不用背景推断。','Attribute prompt not submitted');
  await operation('检测切图');await choose('处理模型','Grounding · r2');
  await page.getByRole('button',{name:'编辑检测提示词',exact:true}).click();await page.getByRole('textbox',{name:'本轮检测提示词',exact:true}).fill('person; face');await page.getByRole('button',{name:'应用提示词',exact:true}).click();body=await submit();check(body.operation==='crop'&&body.prompt==='person; face','Detection prompt not submitted');
  await operation('检测切图');await choose('处理模型','SAM3 · r3');
  check(!await page.getByRole('textbox',{name:'本轮检测提示词',exact:true}).count(),'Unsupported free prompt offered');body=await submit();check(body.prompt==='','Detection prompt leaked into fixed-label model');
  await operation('大模型预标注属性');await choose('处理模型','JEV · r1');
  check(!await page.getByRole('textbox',{name:'本批属性分析补充要求',exact:true}).count(),'JEV free prompt offered');body=await submit();check(body.prompt==='','Attribute prompt leaked into JEV');
  await operation('建立向量索引');await choose('处理模型','Embedding · r1');body=await submit();check(body.prompt==='','Prompt leaked into embedding');
  await operation('图片质量检查');await page.getByRole('button',{name:'编辑质检提示词',exact:true}).click();check((await page.getByRole('textbox',{name:'图片质量要求',exact:true}).inputValue()).includes('车辆'),'Quality requirements overwritten by another operation');await page.getByRole('button',{name:'取消',exact:true}).click();
  check(writes.every(write=>write.path===`/api/projects/${projectId}/work/jobs`),'Model configuration was mutated');
  check(errors.length===0,errors.join('\n'));check(page.url().includes('/projects/'+projectId+'/prepare'),'Prompt editing navigated away');
  await page.screenshot({path:'output/playwright/inline-work-prompts.png',fullPage:true,animations:'disabled'});
  return {checks:10,submittedOperations:writes.map(write=>write.body.operation),modelConfigurationWrites:0,globalRevision:models[0].revision,errors};
}
