async page=>{
  await page.unrouteAll();page.setDefaultTimeout(10000);
  const fixture=await page.evaluate(()=>JSON.parse(localStorage.getItem('codex_probe'))),errors=[],writes=[];
  if(!fixture?.project_id||fixture.asset_ids.length!==3)throw Error('Owned fixture missing');
  page.on('pageerror',error=>errors.push(error.message));
  await page.route(/^http:\/\/localhost:3000\/api\//,route=>{if(route.request().method()!=='GET'){writes.push(route.request().url());return route.abort();}return route.continue();});
  await page.setViewportSize({width:1680,height:1200});await page.goto('http://localhost:3000/annotations?project_id='+fixture.project_id);
  const reviews=page.locator('.ant-card').filter({has:page.getByText('检索匹配标注审核',{exact:true})}).last();
  await reviews.getByText('已通过 2',{exact:true}).waitFor();await reviews.getByText('待人工复核 1',{exact:true}).waitFor();
  await reviews.getByRole('row').filter({hasText:'black.png'}).getByText('已通过',{exact:true}).waitFor();
  await reviews.getByRole('row').filter({hasText:'white.png'}).getByText('已通过',{exact:true}).waitFor();
  const disputed=reviews.getByRole('row').filter({hasText:'blue.png'});await disputed.getByText('待人工复核',{exact:true}).waitFor();
  await disputed.getByText('完整AI审核记录',{exact:true}).click();const json=disputed.getByLabel('匹配AI审核记录',{exact:true});
  if(!(await json.textContent()).includes('\n  "status": "needs_review"'))throw Error('Actual AI evidence not formatted');
  await reviews.screenshot({path:'output/playwright/match-ai-real-qwen.png'});
  await reviews.getByRole('combobox',{name:'匹配审核状态',exact:true}).click();await page.locator('.ant-select-dropdown:visible').getByText('待人工复核',{exact:true}).click();
  if(await reviews.getByRole('button',{name:'通过匹配审核',exact:true}).count()!==1)throw Error('Actual manual queue count wrong');
  await page.setViewportSize({width:390,height:844});await page.waitForFunction(()=>document.documentElement.scrollWidth<=innerWidth);
  await reviews.screenshot({path:'output/playwright/match-ai-live-mobile.png'});
  if(errors.length||writes.length)throw Error(JSON.stringify({errors,writes}));
  return {actualLLM:'Qwen configured in platform',approvedAgreements:2,manualDisagreements:1,jsonEvidenceFormatted:true,browserWrites:0,pageErrors:errors,scope:'Owned three-image fixture; real local APIs; no business annotation or GPU operation'};
}
