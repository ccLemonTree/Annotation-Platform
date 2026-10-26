async page=>{
  await page.unrouteAll();page.setDefaultTimeout(10000);
  const errors=[],writes=[],checks=[],pid='33333333-3333-4333-8333-333333333333',qid='44444444-4444-4444-8444-444444444444',mid='77777777-7777-4777-8777-777777777777';
  const project={id:pid,project_name:'匹配审核验收',task_type:'vector',label_schema:{annotation_scope:'crops'},total_assets:3,annotated_count:0,reviewed_count:0};
  const model={id:mid,key:'qwen-probe',name:'Qwen 视觉审核',kind:'caption',protocol:'openai_chat',enabled:true,revision:1,base_url:'http://model.test',endpoint:'/chat/completions',model_name:'qwen-probe',labels:[],label_mapping:{},timeout_seconds:60,confidence:.3,iou:.5,chat_prompts:{caption:'描述可见内容',review:'审核检测框',match_review:'独立核对主体颜色，不看背景'}};
  const query={id:qid,query:{text:'穿黑色衣服的人'},space:{revision:1},version:1,hits:['correct','incorrect','correct'].map((judgment,i)=>({asset_id:'55555555-5555-4555-8555-'+String(i).padStart(12,'0'),original_name:'crop-'+i+'.png',source_type:'model_crop',judgment,score:.3}))};
  let stage='initial',polls=0,fail=true;
  const job={id:'66666666-6666-4666-8666-666666666666',operation:'match_review',model_id:mid,revision:1,total:3,counts:{pending:3}};
  const long='x'.repeat(1000),config={gpu_uuids:['GPU-test'],parameters:{description:'中文说明',nested:{values:[true,2,null]},long},mounts:[],output_subdir:'train',dataset_path:'/data/train.jsonl'};
  const run={id:'88888888-8888-4888-8888-888888888888',name:'JSON展示验收',kind:'wemm',status:'draft',config,source:{kind:'remote'},server:{id:'default',name:'测试'},inspection:{checks:[{name:'数据检查',ok:true,detail:'{"labels":["人","车"],"count":2}'},{name:'普通文字',ok:true,detail:'目录可以读取'}]},created_at:new Date().toISOString(),output_dir:'/data/run'};
  page.on('pageerror',e=>errors.push(e.message));
  await page.route(/^http:\/\/localhost:3000\/api\//,route=>{
    const req=route.request(),path=new URL(req.url()).pathname,method=req.method(),json=(value,status=200)=>route.fulfill({json:value,status});
    if(method!=='GET')writes.push({path,input:req.postDataJSON()});
    if(path==='/api/auth/me')return json({role:'admin',username:'隔离验收'});
    if(path==='/api/projects')return json({items:[project],total:1});
    if(path==='/api/projects/'+pid)return json(project);
    if(path.endsWith('/stats'))return json({total_assets:3,annotated_count:0,reviewed_count:0});
    if(path==='/api/annotations'||path==='/api/assets')return json({items:[],total:0});
    if(path==='/api/model-services')return json([model]);
    if(path.endsWith('/prompt-defaults'))return json({...model.chat_prompts,scene:'分类场景'});
    if(path.endsWith('/work/jobs'))return json(stage==='initial'?[]:[job]);
    if(path.endsWith('/matches')){
      if(stage==='queued'&&++polls>=2){stage='complete';job.counts={completed:3};query.version++;
        query.hits.forEach((h,i)=>{h.review_status=i===2?'pending':'approved';h.ai_review={status:i===2?'needs_review':'approved',judgment:i===0?'correct':'incorrect',reason:i===2?'候选上衣是白色，与人工判断不同':'衣服颜色证据明确',model_name:model.name,revision:1,reviewed_at:new Date().toISOString()};});}
      return json([query]);
    }
    if(path.endsWith('/matches/review-jobs')){if(fail){fail=false;return json({detail:'已有审核批次，请刷新后重试'},409);}stage='queued';return json([job],201);}
    if(path.endsWith('/review')){const data=req.postDataJSON();if(data.base_version!==query.version)throw Error('Stale review');const hit=query.hits.find(h=>h.asset_id===data.asset_id);hit.review_status=data.approved?'approved':'rejected';hit.ai_review.status='resolved';query.version++;return json(query);}
    if(path.includes('/assets/')&&path.endsWith('/content'))return route.fulfill({contentType:'image/png',body:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aC+sAAAAASUVORK5CYII=','base64')});
    if(path==='/api/training/catalog')return json({yolo26:{name:'YOLO26',schema:{properties:{}},defaults:{},environment:{}},wemm:{name:'WeMM',schema:{properties:{}},defaults:{},environment:{}}});
    if(path==='/api/training/servers')return json([]);
    if(path==='/api/training/runs')return json({items:[run],total:1});
    if(path==='/api/model-services/'+mid&&method==='PUT')return json({...model,...req.postDataJSON(),revision:2});
    return json({detail:'Unexpected fixture '+path},404);
  });
  await page.goto('http://localhost:3000/login');await page.evaluate(()=>{localStorage.setItem('token','isolated');localStorage.setItem('user',JSON.stringify({role:'admin',username:'隔离验收'}));});
  await page.setViewportSize({width:1440,height:1100});await page.goto('http://localhost:3000/annotations?project_id='+pid);
  const reviews=page.locator('.ant-card').filter({has:page.getByText('检索匹配标注审核',{exact:true})}).last();
  const select=async(locator,text)=>{await locator.click();await page.locator('.ant-select-dropdown:visible').getByText(text,{exact:true}).click();};
  await select(reviews.getByRole('combobox',{name:'匹配大模型审核模型'}),'Qwen 视觉审核 · r1');
  await reviews.getByRole('textbox',{name:'匹配大模型审核补充要求'}).fill('仅核对上衣颜色');
  await reviews.getByRole('button',{name:'大模型审核待审匹配',exact:true}).click();await reviews.getByText('已有审核批次，请刷新后重试',{exact:true}).waitFor();
  await reviews.getByRole('button',{name:'大模型审核待审匹配',exact:true}).click();await reviews.getByText('已通过 2',{exact:true}).waitFor();
  await reviews.getByText('待人工复核 1',{exact:true}).waitFor();
  const input=writes.filter(w=>w.path.endsWith('/matches/review-jobs')).at(-1).input;
  if(input.model_id!==mid||input.prompt!=='仅核对上衣颜色'||input.query_ids)throw Error('Wrong all-project batch contract');
  const negative=reviews.getByRole('row').filter({hasText:'crop-1.png'});await negative.getByText('已通过',{exact:true}).waitFor();
  const disputed=reviews.getByRole('row').filter({hasText:'crop-2.png'});await disputed.getByText('待人工复核',{exact:true}).waitFor();
  await disputed.getByText('完整AI审核记录',{exact:true}).click();
  const evidence=disputed.locator('.json-view');await evidence.waitFor();if(!(await evidence.textContent()).includes('\n  "status"'))throw Error('AI JSON not formatted');
  await select(reviews.getByRole('combobox',{name:'匹配审核状态'}),'待人工复核');
  if(await reviews.getByRole('button',{name:'通过匹配审核',exact:true}).count()!==1)throw Error('Manual queue filter failed');
  await page.setViewportSize({width:390,height:844});await page.waitForFunction(()=>document.documentElement.scrollWidth<=innerWidth);
  await reviews.screenshot({path:'output/playwright/match-ai-mobile.png'});
  await disputed.getByRole('button',{name:'通过匹配审核',exact:true}).click();await reviews.getByText('待人工复核 0',{exact:true}).waitFor();
  checks.push('All-project AI queue, failure/retry, positive and negative agreement, disagreement evidence/JSON, live polling, manual filter and resolution, 390px');
  await page.setViewportSize({width:1440,height:1100});await page.goto('http://localhost:3000/training');await page.getByRole('button',{name:'JSON展示验收',exact:true}).click();
  const final=page.getByLabel('保存的最终配置',{exact:true});await final.waitFor();
  if(!(await final.textContent()).includes('\n  "parameters": {\n'))throw Error('Config indentation missing');
  if(!await final.evaluate(n=>n.scrollWidth>n.clientWidth&&getComputedStyle(n).overflowX==='auto'))throw Error('JSON long lines must scroll internally');
  await page.getByRole('tab',{name:'预检查',exact:true}).click();const detail=page.getByLabel('数据检查详情',{exact:true});await detail.waitFor();
  if(!(await detail.textContent()).includes('\n  "labels": [\n'))throw Error('JSON string detail not formatted');
  await page.getByText('目录可以读取',{exact:true}).waitFor();
  checks.push('Training configuration and JSON-string check details indented; long lines scroll; plain text stays readable');
  await page.goto('http://localhost:3000/models');await page.getByRole('button',{name:'编辑',exact:true}).click();
  const editor=page.getByRole('dialog',{name:'编辑模型',exact:true}),mapping=editor.getByRole('textbox',{name:'类别映射',exact:true});
  await editor.getByRole('textbox',{name:'检索匹配审核提示词',exact:true}).fill('只检查上衣颜色');
  await mapping.fill('{"person":"人","car":"车"}');await mapping.press('Tab');
  if(await mapping.inputValue()!=='{\n  "person": "人",\n  "car": "车"\n}')throw Error('JSON editor blur format failed');
  await editor.getByRole('button',{name:'保存模型',exact:true}).click();await editor.waitFor({state:'hidden'});
  const saved=writes.find(w=>w.path==='/api/model-services/'+mid).input;
  if(saved.chat_prompts.match_review!=='只检查上衣颜色'||saved.label_mapping.person!=='人')throw Error('Editable prompt or mapping changed');
  checks.push('Model match-review prompt editable and persisted; JSON editor formats without changing string mapping');
  const formatting=await page.evaluate(async()=>{const {formatJson}=await import('/src/components/formatJson.ts');return [formatJson({a:[1,'中文']}),formatJson('{"a":1}'),formatJson('{invalid'),formatJson(null)];});
  if(formatting[1]!=='{\n  "a": 1\n}'||formatting[2]!=='{invalid'||formatting[3]!=='null')throw Error('JSON formatter edge cases');
  if(errors.length)throw Error(errors.join('\n'));return {checks,pageErrors:errors,scope:'All APIs mocked; no business annotation, model config or GPU writes'};
}
