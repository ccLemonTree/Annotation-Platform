async page=>{
  await page.unrouteAll();page.setDefaultTimeout(10000);
  const fixture=await page.evaluate(()=>JSON.parse(localStorage.getItem('codex_probe'))),errors=[],writes=[];
  if(!fixture?.project_id||fixture.runs.length!==1)throw Error('Owned fixture missing');
  page.on('pageerror',error=>errors.push(error.message));
  await page.route(/^http:\/\/localhost:3000\/api\//,route=>{
    const req=route.request(),path=new URL(req.url()).pathname;
    if(req.method()!=='GET'){writes.push(path);return route.abort();}
    // No SSH resource probe is needed to inspect the saved draft.
    if(path==='/api/gpu-resources')return route.fulfill({json:{gpus:[{uuid:'GPU-probe-owned',index:0,name:'Fixture only',memory_free_mib:0}]}});
    if(path==='/api/training/servers')return route.fulfill({json:[{id:'default',name:'Fixture default',enabled:true}]});
    return route.continue();
  });
  await page.setViewportSize({width:1440,height:1100});await page.goto('http://localhost:3000/annotations?project_id='+fixture.project_id);
  const reviews=page.locator('.ant-card').filter({has:page.getByText('检索匹配标注审核',{exact:true})}).last();
  await reviews.getByText('本页查询 1 条 · 已判定关系 2 条',{exact:true}).waitFor();
  if(await reviews.getByRole('button',{name:'通过匹配审核',exact:true}).count()!==2)throw Error('Actual saved judgments absent');
  await reviews.getByText('已通过 2',{exact:true}).waitFor();
  await page.getByText('暂无图片描述或分类标注；检索匹配标注见上方审核区',{exact:true}).waitFor();
  await reviews.screenshot({path:'output/playwright/matching-review-live.png'});
  await page.goto('http://localhost:3000/training');await page.getByRole('button',{name:'codex_platform_dataset_probe',exact:true}).click();
  await page.getByRole('button',{name:'编辑配置',exact:true}).click();
  const dialog=page.getByRole('dialog',{name:'编辑训练任务配置',exact:true});
  await dialog.getByText('已选择固定版本：1 条训练样本。保存任务不会启动训练。',{exact:true}).waitFor();
  await dialog.getByRole('combobox',{name:'平台数据冻结版本',exact:true}).waitFor();
  await dialog.getByRole('button',{name:'冻结当前标注',exact:true}).scrollIntoViewIfNeeded();
  await page.screenshot({path:'output/playwright/platform-training-live.png'});
  if(writes.length||errors.length)throw Error(JSON.stringify({writes,errors}));
  return {savedJudgments:2,approved:2,imageAnnotations:0,restoredFrozenSamples:1,trainingStatus:'draft',browserWrites:0,pageErrors:errors,scope:'Owned fixture; real local API except GPU/server metadata; no SSH/GPU operation'};
}
