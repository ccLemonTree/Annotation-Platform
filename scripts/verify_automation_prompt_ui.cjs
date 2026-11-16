async page=>{
  await page.unrouteAll({behavior:'ignoreErrors'});page.setDefaultTimeout(10000);
  const base='http://127.0.0.1:3000',projectId='11111111-1111-4111-8111-111111111111',assetId='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
  const defaultReview='审核默认完整核心。'+'默认要求'.repeat(600),defaultDetection='根据正确范例独立检测当前图片内全部项目目标。',writes=[],errors=[];
  const config={base_url:'http://fixture',endpoint:'/detect',model_name:'fixture',enabled:true,timeout_seconds:60,confidence:.25,iou:.45,labels:['car'],label_mapping:{},revision:1};
  const models=[{...config,id:'detector',key:'detector',name:'检测模型甲',kind:'detection',protocol:'model_service'},
    {...config,id:'reviewer-a',key:'reviewer-a',name:'审核模型甲',kind:'caption',protocol:'openai_chat',chat_prompts:{caption:'描述甲',review:defaultReview}},
    {...config,id:'reviewer-b',key:'reviewer-b',name:'审核模型乙',kind:'caption',protocol:'openai_chat',chat_prompts:{caption:'描述乙',review:'乙模型审核核心'}},
    {...config,id:'jev',key:'jev',name:'JEV固定接口',kind:'caption',protocol:'jev_decide'}];
  let deferCreate=true,releaseCreate,createStarted;const started=new Promise(resolve=>createStarted=resolve);
  page.on('pageerror',error=>errors.push(error.message));await page.addInitScript(()=>localStorage.setItem('token','isolated-automation-prompt'));
  await page.route(base+'/api/**',async route=>{
    const request=route.request(),url=new URL(request.url()),path=url.pathname;
    if(request.method()!=='GET'&&path!=='/api/analytics/activity')writes.push({path,method:request.method(),body:request.postDataJSON()});
    if(path==='/api/auth/me')return route.fulfill({json:{id:1,tenant_id:1,username:'管理员',role:'admin'}});
    if(path==='/api/analytics/activity')return route.fulfill({json:{}});
    if(path==='/api/model-services')return route.fulfill({json:models});
    if(path==='/api/model-services/prompt-defaults')return route.fulfill({json:{detection_preannotation:defaultDetection,attributes:'默认属性核心'}});
    if(path==='/api/projects')return route.fulfill({json:{items:[{id:projectId,project_name:'提示词测试项目',task_type:'detection',status:'active',label_schema:{labels:['car']}}],total:1,page:1,page_size:100}});
    if(path.endsWith('/preannotation-examples'))return route.fulfill({json:{items:[{asset_id:assetId,original_name:'人工正确车辆.png',annotation_version:2,task_type:'detection',compatible:true,width:640,height:480,boxes:[{label:'car',x:10,y:20,width:100,height:100}]}],total:1,page:1,page_size:10}});
    if(path.endsWith('/content'))return route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="640" height="480"><rect width="640" height="480" fill="#314c74"/></svg>'});
    if(path==='/api/automation/jobs'){
      if(request.method()==='POST'){
        if(deferCreate){createStarted();await new Promise(resolve=>releaseCreate=resolve);}
        return route.fulfill({json:{id:'created-job',...request.postDataJSON(),status:'pending',total:1,processed:0,approved_count:0,needs_review_count:0,failed_count:0,created_at:'2026-11-06T00:00:00Z'}});
      }
      return route.fulfill({json:{items:[],total:0,page:1,page_size:20}});
    }
    if(request.method()!=='GET')throw Error('Unexpected write '+path);
    return route.fulfill({json:[]});
  });
  const dialog=()=>page.getByRole('dialog',{name:'创建预标注 / AI审核任务',exact:true});
  const editor=title=>page.getByRole('dialog',{name:title,exact:true});
  const openEditor=async(button,title,label)=>{await dialog().getByRole('button',{name:button,exact:true}).click();const input=editor(title).getByRole('textbox',{name:label,exact:true});await input.waitFor();return input;};
  const apply=title=>editor(title).getByRole('button',{name:'应用提示词',exact:true}).click();
  const core=()=>editor('本次审核核心提示词').getByRole('textbox',{name:'审核核心提示词',exact:true});
  const supplement=()=>editor('本次审核补充要求').getByRole('textbox',{name:'审核提示词',exact:true});
  const examplePrompt=()=>editor('本次范例预标注补充要求').getByRole('textbox',{name:'范例预标注补充要求',exact:true});
  const choose=async(name,label)=>{await dialog().getByRole('combobox',{name,exact:true}).click();await page.locator('.ant-select-dropdown:visible .ant-select-item-option').filter({hasText:label}).click();};
  const open=()=>page.getByRole('button',{name:'创建任务',exact:true}).click();
  const submit=()=>dialog().getByRole('button',{name:/提交任务/}).click();
  const requireValue=async(locator,value,message)=>{if(await locator.inputValue()!==value)throw Error(message);};
  await page.setViewportSize({width:1440,height:1050});await page.goto(base+'/automation?project_id='+projectId);await open();
  await openEditor('编辑审核提示词','本次审核核心提示词','审核核心提示词');await requireValue(core(),defaultReview,'Initial reviewer did not load full default');
  if(await core().getAttribute('maxlength')!=='8000')throw Error('Wrong core prompt limit');
  await core().fill('本任务改写核心');await editor('本次审核核心提示词').getByRole('button',{name:'恢复默认提示词',exact:true}).click();
  await requireValue(core(),defaultReview,'Restore did not restore model default');
  await apply('本次审核核心提示词');await choose('审核模型','审核模型乙');await openEditor('编辑审核提示词','本次审核核心提示词','审核核心提示词');await requireValue(core(),'乙模型审核核心','Reviewer change kept previous prompt');await apply('本次审核核心提示词');
  await choose('任务类型','AI审核');await openEditor('编辑审核提示词','本次审核核心提示词','审核核心提示词');await core().fill('只审核完整车辆');
  await page.screenshot({path:'output/playwright/automation-review-prompt.png',fullPage:true,animations:'disabled'});await apply('本次审核核心提示词');
  await openEditor('编辑审核补充要求','本次审核补充要求','审核提示词');await supplement().fill('车牌看不清不算漏标');await apply('本次审核补充要求');
  await submit();await started;if(!await dialog().getByRole('button',{name:'编辑审核提示词',exact:true}).isDisabled()||!await dialog().getByRole('button',{name:'编辑审核补充要求',exact:true}).isDisabled())throw Error('Submitting permits prompt edits');
  releaseCreate();deferCreate=false;await dialog().waitFor({state:'hidden'});
  let payload=writes.at(-1).body;
  if(payload.kind!=='review'||payload.reviewer_id!=='reviewer-b'||payload.config.review_prompt!=='只审核完整车辆'||payload.config.prompt!=='车牌看不清不算漏标'||payload.config.preannotation_prompt)throw Error('Review prompt payload incorrect');
  await open();await openEditor('编辑审核提示词','本次审核核心提示词','审核核心提示词');await core().fill('不应提交的审核核心');await apply('本次审核核心提示词');
  await openEditor('编辑审核补充要求','本次审核补充要求','审核提示词');await supplement().fill('不应提交的审核补充');await apply('本次审核补充要求');
  await dialog().getByRole('checkbox',{name:'预标注后自动调用AI审核',exact:true}).uncheck();
  await dialog().getByRole('button',{name:'编辑审核提示词',exact:true}).waitFor({state:'hidden'});
  if(await dialog().getByRole('button',{name:'编辑审核提示词',exact:true}).count()||await dialog().getByRole('button',{name:'编辑审核补充要求',exact:true}).count())throw Error('Inactive review prompts remain editable');
  await submit();await dialog().waitFor({state:'hidden'});payload=writes.at(-1).body;
  if(payload.kind!=='preannotate'||payload.reviewer_id||payload.config.review_prompt||payload.config.prompt||payload.config.preannotation_prompt||payload.config.detection_prompt||payload.config.auto_review)throw Error('Hidden review prompt leaked to plain detection');
  await open();await dialog().getByRole('checkbox',{name:'大模型按正确范例预标注检测框',exact:true}).check();
  await dialog().getByRole('button',{name:'编辑目标检测提示词',exact:true}).waitFor();await choose('范例预标注大模型','审核模型甲');
  let detection=await openEditor('编辑目标检测提示词','范例目标检测核心提示词','目标检测核心提示词');await requireValue(detection,defaultDetection,'Detection core default missing');await detection.fill('只检测清晰的完整车辆目标');await apply('范例目标检测核心提示词');
  await openEditor('编辑检测补充要求','本次范例预标注补充要求','范例预标注补充要求');
  await editor('本次范例预标注补充要求').getByText('仅补充本次预标注要求；返回结构和坐标规则保持不变，不修改模型配置。',{exact:true}).waitFor();
  if(await dialog().getByRole('button',{name:'编辑审核提示词',exact:true}).count()||await examplePrompt().getAttribute('maxlength')!=='8000')throw Error('Example mode exposes unrelated prompt or wrong limit');
  await examplePrompt().fill('仅标注前景完整车辆');await apply('本次范例预标注补充要求');
  await dialog().getByRole('button',{name:'选择正确范例（可选）',exact:true}).click();
  await page.getByRole('dialog',{name:'选择人工正确范例',exact:true}).getByRole('button',{name:'使用此范例',exact:true}).click();
  await openEditor('编辑目标检测提示词','范例目标检测核心提示词','目标检测核心提示词');await page.screenshot({path:'output/playwright/automation-example-prompt.png',fullPage:true,animations:'disabled'});await apply('范例目标检测核心提示词');
  await submit();await dialog().waitFor({state:'hidden'});payload=writes.at(-1).body;
  if(payload.preannotation_model_id!=='reviewer-a'||payload.example_asset_id!==assetId||payload.example_annotation_version!==2||payload.reviewer_id||payload.config.detection_prompt!=='只检测清晰的完整车辆目标'||payload.config.preannotation_prompt!=='仅标注前景完整车辆'||payload.config.review_prompt||payload.config.prompt||payload.config.auto_review)throw Error('Example prompt payload incorrect');
  if(writes.some(write=>write.path!=='/api/automation/jobs'||write.method!=='POST'))throw Error('Task prompt editing wrote global config');
  if(errors.length)throw Error(JSON.stringify(errors));
  return {checks:['review and detection prompts edited in dialogs','initial full review model default','actual detection core and supplement separate','8000 core and example limit','restore model default','reviewer switch resets core','review core and legacy supplement separate','submitting locks editing','inactive review fields do not leak','example supplemental prompt retains fixed contract guidance','example payload retains example version and excludes reviewer fields','task-only writes'],jobs:writes.length,errors};
}
