async page=>{
  page.setDefaultTimeout(12000);
  const base='http://localhost:3000',ids={classification:'11111111-1111-4111-8111-111111111111',caption:'22222222-2222-4222-8222-222222222222',vector:'33333333-3333-4333-8333-333333333333',detection:'44444444-4444-4444-8444-444444444444'};
  const checks=[],requests=[],writes=[],unexpected=[],errors=[],snapshots={};
  const user={id:1,username:'freeze-fixture',role:'admin',tenant_id:1,is_active:true};
  const project=id=>({id,project_name:'冻结默认值 '+Object.keys(ids).find(key=>ids[key]===id),task_type:Object.keys(ids).find(key=>ids[key]===id),status:'active',label_schema:{labels:['人']},total_assets:2,annotated_count:2,reviewed_count:2});
  const template=(name,defaults)=>({name,image:'fixture/model',defaults,environment:{},schema:{properties:{task_type:{type:'string',enum:['causal_lm','embedding']},loss_type:{type:'string',enum:['infonce','cosine_similarity']}}}});
  const check=(ok,label)=>{if(!ok)throw Error(label);checks.push(label);};
  const api=/^https?:\/\/[^/]+\/api\//;
  const handler=async route=>{
    const req=route.request(),url=new URL(req.url()),path=url.pathname,method=req.method();let body;try{body=req.postDataJSON();}catch{}
    requests.push({path,method,search:url.search});if(method!=='GET')writes.push({path,method,body});let data,status=200;
    if(path==='/api/auth/me')data=user;
    else if(path.startsWith('/api/projects/'))data=project(path.split('/').at(-1));
    else if(path==='/api/training/catalog')data={yolo26:template('YOLO26',{epochs:2,batch:4}),swift_sft:template('Swift SFT',{task_type:'causal_lm',loss_type:null}),wemm:template('WeMM',{task_type:'embedding',loss_type:'infonce'})};
    else if(path==='/api/training/servers')data=[{id:'default',name:'隔离服务器',enabled:true}];
    else if(path==='/api/training/defaults')data={model_path:'/fixture/model.pt',output_root:'/fixture/runs'};
    else if(path==='/api/gpu-resources')data={server_name:'fixture',gpus:[],processes:[],warnings:[]};
    else if(path==='/api/training/runs')data={items:[],total:0,page:1,page_size:20};
    else if(/^\/api\/export\/projects\/[^/]+\/summary$/.test(path))data={labels:['人'],all:{images:2,images_by_label:{人:1}},approved:{images:2,images_by_label:{人:1}}};
    else if(/^\/api\/export\/projects\/[^/]+\/splits\/preview$/.test(path)){
      const mode=body.selection.mode,small=body.selection.ratios.train===80;
      const counts=mode==='val_only'?{val:2}:mode==='train_only'||small?{train:2}:{train:1,val:1};
      data={can_freeze:true,images_by_split:counts,excluded_asset_ids:[],labels_by_split:{train:{人:counts.train||0},val:{人:counts.val||0},test:{人:0}}};
    }else if(/^\/api\/export\/projects\/[^/]+\/annotation-snapshots$/.test(path)){
      const id=path.split('/')[4],snapshot={id:'55555555-5555-4555-8555-555555555555',format:body.dataset_format,created_at:'2026-11-06T00:00:00Z',manifest:{training:{format:body.dataset_format,source:body.sample_source,samples:2}}};
      snapshots[id]=[snapshot];data=snapshot;status=201;
    }else if(/^\/api\/export\/projects\/[^/]+\/snapshots$/.test(path)){
      const id=path.split('/')[4];
      if(method==='POST'){const row={id:'66666666-6666-4666-8666-666666666666',format:body.format,asset_count:2,created_at:'2026-11-06T00:00:00Z'};snapshots[id]=[row];data=row;status=201;}
      else data=snapshots[id]||[];
    }else{unexpected.push(`${method} ${path}`);return route.fulfill({status:599,json:{detail:'未配置的隔离API'}});}
    return route.fulfill({status,json:data});
  };
  const onError=error=>errors.push(error.message);page.on('pageerror',onError);
  await page.addInitScript(user=>{if(!location.protocol.startsWith('http'))return;localStorage.setItem('token','isolated-freeze-defaults');localStorage.setItem('user',JSON.stringify(user));},user);
  await page.route(api,handler);
  const select=async(locator,text)=>{await locator.click();await page.locator('.ant-select-dropdown:visible').getByText(text,{exact:true}).click();};
  const openTraining=async kind=>{await page.goto(`${base}/projects/${ids[kind]}/deliver`);await page.getByRole('button',{name:'创建训练任务',exact:true}).click();const form=page.getByRole('dialog',{name:'创建训练任务',exact:true});await form.waitFor();return form;};
  try{
    await page.setViewportSize({width:1440,height:1000});
    let form=await openTraining('classification');await form.getByText('分类标签配对',{exact:true}).waitFor();
    check(await form.getByRole('textbox',{name:'平台SFT训练指令',exact:true}).inputValue()==='请识别图片中的类别，以JSON数组回答。','分类Swift默认分类标签和分类指令');
    await select(form.getByRole('combobox',{name:'标注数据来源',exact:true}),'图片描述配对');
    await form.getByRole('textbox',{name:'平台SFT训练指令',exact:true}).fill('旧格式自定义指令');
    await select(form.getByRole('combobox',{name:'训练器'}),'WeMM');await form.getByText('分类标签配对',{exact:true}).waitFor();
    check(await form.getByRole('textbox',{name:'平台SFT训练指令',exact:true}).count()===0,'分类切换向量格式后重置为合法分类来源');
    await select(form.getByRole('combobox',{name:'训练器'}),'Swift SFT');await form.getByText('分类标签配对',{exact:true}).waitFor();
    check(await form.getByRole('textbox',{name:'平台SFT训练指令',exact:true}).inputValue()==='请识别图片中的类别，以JSON数组回答。','切回Swift不会残留旧来源或自定义指令');
    await form.getByRole('button',{name:'冻结当前标注',exact:true}).click();await form.getByText(/已选择固定版本/).waitFor();
    const frozen=writes.find(row=>row.path.endsWith('/annotation-snapshots'));
    check(frozen.body.sample_source==='labels'&&frozen.body.instruction==='请识别图片中的类别，以JSON数组回答。'&&frozen.body.approved_only===true,'分类冻结提交正确来源、指令和默认审核范围');
    await page.keyboard.press('Escape');
    form=await openTraining('caption');await form.getByText('图片描述配对',{exact:true}).waitFor();
    check(await form.getByRole('textbox',{name:'平台SFT训练指令',exact:true}).inputValue()==='请描述这张图片。','切到描述项目不残留分类来源或指令');
    await page.keyboard.press('Escape');
    form=await openTraining('vector');await form.getByText('检索匹配判定（正确 / 错误）',{exact:true}).waitFor();
    check(true,'向量InfoNCE默认检索匹配判定');
    await select(form.getByRole('combobox',{name:'训练器'}),'Swift SFT');await form.getByText('图片描述配对',{exact:true}).waitFor();
    check(await form.getByText('检索匹配判定（正确 / 错误）',{exact:true}).count()===0,'向量切Swift后清除不支持的matches来源');
    await page.keyboard.press('Escape');

    await page.goto(`${base}/projects/${ids.detection}/deliver`);await page.getByRole('button',{name:'导出 / 冻结数据集',exact:true}).click();
    const dialog=page.getByRole('dialog',{name:'检测数据导出',exact:true});await dialog.waitFor();
    await dialog.getByRole('combobox',{name:'导出格式',exact:true}).waitFor();await dialog.getByRole('combobox',{name:'导出类别',exact:true}).waitFor();
    await dialog.getByRole('spinbutton',{name:'人 每类训练图片预算',exact:true}).waitFor();check(true,'导出格式、导出类别和每类训练预算均有可见标签与可访问名称');
    await dialog.getByRole('button',{name:'预览当前划分',exact:true}).click();await dialog.getByText('可导出，暂不可训练：缺少验证集',{exact:true}).waitFor();
    check(await dialog.getByRole('button',{name:'仅冻结',exact:true}).isEnabled(),'两张图默认划分明确缺验证集但保留合法冻结');
    await dialog.getByRole('button',{name:'调整划分',exact:true}).click();
    check(await dialog.getByRole('combobox',{name:'划分方式',exact:true}).evaluate(node=>node===document.activeElement),'调整划分入口可直接聚焦划分方式');
    for(const [name,value] of [['训练集比例（%）','50'],['验证集比例（%）','50'],['测试集比例（%）','0']]){const input=dialog.getByRole('spinbutton',{name,exact:true});await input.fill(value);await input.press('Tab');}
    await dialog.getByRole('button',{name:'预览当前划分',exact:true}).click();await dialog.getByText('可导出，划分满足训练要求',{exact:true}).waitFor();
    check(true,'补足train/val后预览明确划分满足训练要求');
    await select(dialog.getByRole('combobox',{name:'划分方式',exact:true}),'仅训练集');await dialog.getByRole('button',{name:'预览当前划分',exact:true}).click();await dialog.getByText('可导出，暂不可训练：缺少验证集',{exact:true}).waitFor();
    await dialog.getByRole('button',{name:'仅冻结',exact:true}).click();await page.getByText('冻结版本已保存',{exact:true}).waitFor();check(true,'仅训练集仍可正常冻结');
    await select(dialog.getByRole('combobox',{name:'划分方式',exact:true}),'仅验证集');await dialog.getByRole('button',{name:'预览当前划分',exact:true}).click();await dialog.getByText('可导出，暂不可训练：缺少训练集',{exact:true}).waitFor();
    check(await dialog.getByRole('button',{name:'仅冻结',exact:true}).isEnabled(),'仅验证集给出缺训练集说明并保留冻结入口');
    await page.keyboard.press('Escape');await page.setViewportSize({width:390,height:844});await page.waitForTimeout(350);check(await page.locator('html').evaluate(node=>node.scrollWidth<=node.clientWidth+1),'390px导出表单不造成页面横向溢出');
    await page.screenshot({path:'output/playwright/training-freeze-defaults-390.png',fullPage:true,animations:'disabled'});
    await dialog.getByText('可导出，暂不可训练：缺少训练集',{exact:true}).scrollIntoViewIfNeeded();await page.screenshot({path:'output/playwright/training-freeze-defaults-warning-390.png',fullPage:true,animations:'disabled'});
    await page.setViewportSize({width:320,height:844});await page.waitForTimeout(350);check(await page.locator('html').evaluate(node=>node.scrollWidth<=node.clientWidth+1),'320px导出表单不造成页面横向溢出');await dialog.getByRole('button',{name:'调整划分',exact:true}).scrollIntoViewIfNeeded();await page.screenshot({path:'output/playwright/training-freeze-defaults-warning-320.png',fullPage:true,animations:'disabled'});
    check(unexpected.length===0&&errors.length===0,'全部API严格拦截且无浏览器异常');
    check(writes.every(row=>row.path.startsWith('/api/export/')),'只调用模拟预览或冻结，不提交训练或业务写入');
    return {checks,requests,writes,unexpected,errors,scope:'all API requests intercepted; only fixture previews/freezes; no real DB, SSH or training'};
  }catch(error){await page.screenshot({path:'output/playwright/training-freeze-defaults-failure.png',fullPage:true});throw error;}finally{await page.goto('about:blank');await page.unroute(api,handler);page.off('pageerror',onError);}
}
