async page=>{
  await page.unrouteAll({behavior:'ignoreErrors'});page.setDefaultTimeout(10000);
  const base='http://127.0.0.1:3000',projectId='11111111-1111-4111-8111-111111111111',assetId='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
  const schema={target_label:'car',fields:[{key:'plate',name:'车牌',instruction:'车牌不可见时无法判断',options:[]}]};
  const defaultPrompt='请分析当前图片中的车辆。'+ '完整默认提示词'.repeat(350),attributeCore='按项目属性配置判断可见车辆，无法确认时不要猜测。';
  const models=[{id:'33333333-3333-4333-8333-333333333333',name:'Qwen甲',revision:4,unavailable_reason:null,supports_prompt:true,prompt:defaultPrompt},
    {id:'44444444-4444-4444-8444-444444444444',name:'Qwen乙',revision:2,unavailable_reason:null,supports_prompt:true,prompt:'乙模型默认描述要求'},
    {id:'55555555-5555-4555-8555-555555555555',name:'固定接口模型',revision:1,unavailable_reason:null,supports_prompt:false,prompt:''},
    {id:'66666666-6666-4666-8666-666666666666',name:'JEV属性选择',revision:1,unavailable_reason:'JEV不支持当前分析',supports_prompt:false,prompt:''}];
  const writes=[],errors=[];let deferModels=false,releaseModels,deferAnalysis=false,releaseAnalysis;
  page.on('pageerror',error=>errors.push(error.message));await page.addInitScript(()=>localStorage.setItem('token','isolated-single-image-prompt'));
  await page.route(base+'/api/**',async route=>{
    const request=route.request(),url=new URL(request.url()),path=url.pathname;
    if(request.method()!=='GET')writes.push({path,method:request.method(),body:request.postDataJSON()});
    if(path==='/api/auth/me')return route.fulfill({json:{id:2,tenant_id:1,username:'标注员',role:'user'}});
    if(path==='/api/analytics/activity')return route.fulfill({json:{}});
    if(path==='/api/projects/'+projectId)return route.fulfill({json:{id:projectId,project_name:'单图提示词测试',task_type:'caption',status:'active',label_schema:{object_attributes:schema,labels:['car']}}});
    if(path==='/api/assets/collections')return route.fulfill({json:[]});
    if(path.startsWith('/api/assets/')){
      if(path.endsWith('/content'))return route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="640" height="480"><rect width="640" height="480" fill="#314c74"/></svg>'});
      if(path.endsWith('/neighbors'))return route.fulfill({json:{position:1,total:1,previous_id:null,next_id:null}});
      return route.fulfill({json:{id:assetId,project_id:projectId,original_name:'提示词测试.png',width:640,height:480,source_type:'model_crop',annotation_status:'pending'}});
    }
    if(path.endsWith('/analysis-models')){
      if(deferModels)await new Promise(resolve=>releaseModels=resolve);
      return route.fulfill({json:models.map(model=>({...model,prompt:url.searchParams.get('mode')==='description'?model.prompt:attributeCore}))});
    }
    if(path.endsWith('/analyze')){
      if(deferAnalysis)await new Promise(resolve=>releaseAnalysis=resolve);
      const body=request.postDataJSON();return route.fulfill({json:{asset_id:assetId,model_id:body.model_id,model_revision:body.model_revision,...(body.mode==='attributes'?{attribute_schema:schema,attributes:{plate:{value:'苏L12345',reason:'清晰可见'}}}:{description:'本次模型描述'})}});
    }
    if(path==='/api/annotations')return route.fulfill({json:{items:[{id:'ann-one',asset_id:assetId,version:1,is_latest:true,review_status:'pending',annotation_source:'manual',annotation_data:{description:'已有描述',labels:['car'],attribute_schema:schema,attributes:{plate:{value:'人工标注',reason:''}}}}],total:1}});
    if(request.method()!=='GET')throw Error('Unexpected write '+path);
    return route.fulfill({json:[]});
  });
  const dialog=()=>page.getByRole('dialog',{name:'大模型分析当前图片',exact:true});
  const editor=title=>page.getByRole('dialog',{name:title,exact:true});
  const openEditor=async(button,title,label)=>{await dialog().getByRole('button',{name:button,exact:true}).click();const input=editor(title).getByRole('textbox',{name:label,exact:true});await input.waitFor();return input;};
  const apply=title=>editor(title).getByRole('button',{name:'应用提示词',exact:true}).click();
  const prompt=()=>editor('本次图片描述提示词').getByRole('textbox',{name:'本次提示词',exact:true});
  const supplement=()=>editor('本次属性分析补充要求').getByRole('textbox',{name:'补充分析要求',exact:true});
  const choose=async(name,label)=>{await dialog().getByRole('combobox',{name,exact:true}).click();await page.locator('.ant-select-dropdown:visible .ant-select-item-option').filter({hasText:label}).click();};
  const start=()=>dialog().getByRole('button',{name:/开始分析/}).click();
  const preview=()=>dialog().getByText('分析结果（尚未保存）',{exact:true}).waitFor();
  const requireValue=async(locator,value,message)=>{if(await locator.inputValue()!==value)throw Error(message);};
  await page.setViewportSize({width:1440,height:1000});await page.goto(base+'/annotate/'+assetId);
  const attribute=page.locator('.image-annotation-fields').getByLabel('车牌属性值',{exact:true});await attribute.waitFor();await attribute.fill('人工草稿');
  await page.getByRole('button',{name:'大模型分析当前图片',exact:true}).click();
  await choose('单图分析模型','Qwen甲');
  if(await dialog().locator('textarea').count())throw Error('Analysis page still has inline prompt editor');
  let core=await openEditor('编辑属性分析提示词','本次属性分析核心提示词','属性分析核心提示词');
  await requireValue(core,attributeCore,'Actual attribute core not loaded');await core.fill('取消的核心改动');
  await editor('本次属性分析核心提示词').getByRole('button',{name:/^取\s*消$/}).click();
  core=await openEditor('编辑属性分析提示词','本次属性分析核心提示词','属性分析核心提示词');
  await requireValue(core,attributeCore,'Cancel changed attribute core');await core.fill('本次核心：优先核对车牌，不猜测不可见内容');await apply('本次属性分析核心提示词');
  await openEditor('编辑属性补充要求','本次属性分析补充要求','补充分析要求');await requireValue(supplement(),'','Attribute supplement should start empty');
  await supplement().fill('只识别车辆后方可见的车牌');await apply('本次属性分析补充要求');await start();await preview();
  if(writes.at(-1).body.attribute_prompt!=='本次核心：优先核对车牌，不猜测不可见内容'||writes.at(-1).body.prompt!=='只识别车辆后方可见的车牌'||writes.at(-1).body.mode!=='attributes')throw Error('Attribute core and supplement were not separate');
  await requireValue(attribute,'人工草稿','Analysis preview changed annotation draft');
  await choose('分析内容','描述当前图片');await choose('单图分析模型','Qwen甲');
  await openEditor('编辑图片描述提示词','本次图片描述提示词','本次提示词');await requireValue(prompt(),defaultPrompt,'Full model default was truncated or not loaded');
  if(await dialog().getByText('分析结果（尚未保存）',{exact:true}).count())throw Error('Mode change kept old preview');
  if(await prompt().getAttribute('maxlength')!=='8000')throw Error('Wrong prompt limit');
  await editor('本次图片描述提示词').getByText('仅用于本次分析，不修改模型的默认提示词。',{exact:true}).waitFor();
  await prompt().fill('只描述车辆是否完整');await apply('本次图片描述提示词');deferAnalysis=true;await start();
  await page.waitForFunction(()=>!!document.querySelector('.ant-modal .ant-select-disabled'));
  if(!await dialog().getByRole('button',{name:'编辑图片描述提示词',exact:true}).isDisabled()||!await dialog().getByRole('combobox',{name:'单图分析模型',exact:true}).isDisabled())throw Error('Analysis permits prompt/model edits');
  releaseAnalysis();deferAnalysis=false;await preview();
  if(writes.at(-1).body.prompt!=='只描述车辆是否完整'||writes.at(-1).body.mode!=='description')throw Error('Custom description prompt was not sent');
  await choose('单图分析模型','Qwen乙');await openEditor('编辑图片描述提示词','本次图片描述提示词','本次提示词');await requireValue(prompt(),'乙模型默认描述要求','Model change reused previous override');await apply('本次图片描述提示词');
  if(await dialog().getByText('分析结果（尚未保存）',{exact:true}).count())throw Error('Model change kept old preview');
  await start();await preview();await openEditor('编辑图片描述提示词','本次图片描述提示词','本次提示词');await prompt().fill('改动后需要重新分析');await apply('本次图片描述提示词');
  if(await dialog().getByText('分析结果（尚未保存）',{exact:true}).count())throw Error('Prompt edit kept stale preview');
  await choose('单图分析模型','固定接口模型');
  await dialog().getByText('当前模型使用固定接口，不支持自由提示词。',{exact:true}).waitFor();
  if(await dialog().getByRole('button',{name:'编辑图片描述提示词',exact:true}).count())throw Error('Fixed protocol has editable free prompt');
  await start();await preview();if('prompt' in writes.at(-1).body)throw Error('Fixed protocol received a prompt');
  await dialog().getByRole('combobox',{name:'单图分析模型',exact:true}).click();
  if(await page.locator('.ant-select-dropdown:visible .ant-select-item-option').filter({hasText:'JEV属性选择'}).getAttribute('aria-disabled')!=='true')throw Error('Unsupported JEV model selectable');
  await page.keyboard.press('Escape');await choose('单图分析模型','Qwen甲');
  await openEditor('编辑图片描述提示词','本次图片描述提示词','本次提示词');await prompt().fill('模型切换前覆盖');await apply('本次图片描述提示词');await choose('分析内容','分析项目属性');await choose('单图分析模型','Qwen甲');
  await openEditor('编辑属性补充要求','本次属性分析补充要求','补充分析要求');await requireValue(supplement(),'','Description prompt leaked into attributes');await supplement().fill('刷新前补充要求');await apply('本次属性分析补充要求');
  deferModels=true;await dialog().getByRole('button',{name:'刷新模型',exact:true}).click();
  if(!await dialog().getByRole('button',{name:'编辑属性补充要求',exact:true}).isDisabled())throw Error('Model loading allows prompt edits');
  await page.waitForFunction(()=>document.querySelector('.ant-modal .ant-select-loading')!==null);releaseModels();deferModels=false;
  await page.waitForFunction(()=>!document.querySelector('.ant-modal .ant-select-loading'));
  await openEditor('编辑属性补充要求','本次属性分析补充要求','补充分析要求');await requireValue(supplement(),'','Model refresh reused old override');await apply('本次属性分析补充要求');
  await choose('分析内容','描述当前图片');await choose('单图分析模型','Qwen甲');
  await openEditor('编辑图片描述提示词','本次图片描述提示词','本次提示词');
  await page.screenshot({path:'output/playwright/current-image-prompt.png',fullPage:true,animations:'disabled'});
  await editor('本次图片描述提示词').getByRole('button',{name:/^取\s*消$/}).click();await dialog().getByRole('button',{name:'关闭',exact:true}).click();await requireValue(attribute,'人工草稿','Closing changed draft');
  if(writes.some(write=>!write.path.endsWith('/analyze')))throw Error('Prompt editing wrote annotation or global model config');
  if(errors.length)throw Error(JSON.stringify(errors));
  return {checks:['attribute actual core and supplement separate','cancel preserves prompt','all prompt editing in dialogs','full default longer than 2000 preserved','8000 character limit','per-request payload only','analysis and loading lock editing','mode and model changes reset prompts/results','prompt edits invalidate stale preview','fixed protocols disallow prompt','unsupported JEV disabled','refresh resets override','no annotation/model configuration writes','preview and close preserve draft'],requests:writes.length,errors};
}
