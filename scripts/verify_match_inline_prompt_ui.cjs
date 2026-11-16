async page=>{
  await page.unrouteAll({behavior:'ignoreErrors'});page.setDefaultTimeout(8000);
  const base='http://127.0.0.1:3000',pid='33333333-3333-4333-8333-333333333333',mid='77777777-7777-4777-8777-777777777777';
  const model={id:mid,name:'Qwen审核',kind:'caption',protocol:'openai_chat',enabled:true,revision:1,chat_prompts:{match_review:'独立核对可见证据。'.repeat(260)}};
  const project={id:pid,project_name:'匹配提示词',task_type:'vector',label_schema:{},total_assets:1,annotated_count:0,reviewed_count:0},writes=[],errors=[];
  await page.addInitScript(()=>localStorage.setItem('token','isolated-inline-match'));page.on('pageerror',e=>errors.push(e.message));
  await page.route(base+'/api/**',route=>{
    const req=route.request(),path=new URL(req.url()).pathname,json=value=>route.fulfill({json:value});
    if(path==='/api/auth/me')return json({id:1,role:'admin',username:'管理员'});
    if(path==='/api/projects/'+pid)return json(project);
    if(path.endsWith('/stats'))return json({total_assets:1,annotated_count:0,reviewed_count:0});
    if(path==='/api/annotations')return json({items:[],total:0});
    if(path==='/api/model-services')return json([model]);
    if(path.endsWith('/matches/review-jobs')){writes.push({path,body:req.postDataJSON()});return json([]);}
    if(req.method()!=='GET')throw Error('Unexpected write '+req.method()+' '+path);
    return json([]);
  });
  await page.setViewportSize({width:1440,height:1080});await page.goto(base+'/annotations?project_id='+pid);
  await page.getByRole('combobox',{name:'匹配大模型审核模型',exact:true}).click();await page.locator('.ant-select-dropdown:visible').getByText('Qwen审核 · r1',{exact:true}).click();
  if(await page.getByRole('textbox',{name:'本批匹配审核核心提示词',exact:true}).count())throw Error('Inline core editor remains');
  await page.getByRole('button',{name:'编辑匹配审核提示词',exact:true}).click();
  const coreDialog=page.getByRole('dialog',{name:'本批匹配审核核心提示词',exact:true}),editor=coreDialog.getByRole('textbox',{name:'本批匹配审核核心提示词',exact:true});await editor.waitFor();
  if(await editor.inputValue()!==model.chat_prompts.match_review)throw Error('Full default prompt not displayed');
  await editor.fill('只核对目标衣服颜色。');await coreDialog.getByRole('button',{name:'恢复默认提示词',exact:true}).click();
  if(await editor.inputValue()!==model.chat_prompts.match_review)throw Error('Restore lost default');
  await editor.fill('本批只核对目标衣服颜色。');await coreDialog.getByRole('button',{name:'应用提示词',exact:true}).click();
  await page.getByRole('button',{name:'编辑匹配补充要求',exact:true}).click();const extra=page.getByRole('dialog',{name:'匹配大模型审核补充要求',exact:true});
  await extra.getByRole('textbox',{name:'匹配大模型审核补充要求',exact:true}).fill('遮挡时无法判断。');await extra.getByRole('button',{name:'应用提示词',exact:true}).click();
  if(await page.getByRole('link',{name:'编辑模型核心提示词',exact:true}).count())throw Error('Core prompt still links to model management');
  await page.getByRole('button',{name:'大模型审核待审匹配',exact:true}).click();await page.getByText(/已排队 0 条匹配关系/).waitFor();
  if(writes.length!==1||writes[0].body.review_prompt!=='本批只核对目标衣服颜色。'||writes[0].body.prompt!=='遮挡时无法判断。')throw Error('Core override and supplementary prompt contract changed');
  if(model.revision!==1||!page.url().includes('/annotations?project_id='))throw Error('Global model or current page changed');
  await page.setViewportSize({width:390,height:844});await page.waitForFunction(()=>document.documentElement.scrollWidth<=innerWidth);
  await page.getByRole('button',{name:'收起导航',exact:true}).click().catch(()=>{});
  await page.screenshot({path:'output/playwright/match-inline-prompts-mobile.png',fullPage:true,animations:'disabled'});
  if(errors.length)throw Error(errors.join('\n'));return {checks:6,modelConfigurationWrites:0,coreAndSupplementSeparate:true,fullDefaultLength:model.chat_prompts.match_review.length,errors};
}
