async page=>{
  await page.unrouteAll();await page.emulateMedia({reducedMotion:'no-preference'});page.setDefaultTimeout(10000);
  const projectId='33333333-3333-4333-8333-333333333333';
  const rows=Array.from({length:21},(_,i)=>({id:`aaaaaaaa-aaaa-4aaa-8aaa-${String(i).padStart(12,'0')}`,project_id:projectId,original_name:`crop-${i}.png`,source_type:'model_crop',annotation_status:'pending'}));
  const project={id:projectId,project_name:'全部删除验收',task_type:'vector',label_schema:{annotation_scope:'crops'},total_assets:21,annotated_count:0,reviewed_count:0};
  const png=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aC+sAAAAASUVORK5CYII=','base64');
  let removed=new Set(),fail=false,reads=0;const batches=[],checks=[],errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  await page.route(/^http:\/\/localhost:3000\/api\//,route=>{
    const req=route.request(),url=new URL(req.url()),path=url.pathname,json=(value,status=200)=>route.fulfill({json:value,status});
    if(path==='/api/batch/assets/delete'){const ids=req.postDataJSON();batches.push(ids);if(fail){fail=false;return json({detail:'批量删除失败，请重试'},409);}ids.forEach(id=>removed.add(id));return json({success:true,affected_count:ids.length});}
    if(req.method()!=='GET')throw Error('Unexpected mutation '+path);
    if(path.endsWith('/content'))return route.fulfill({contentType:'image/png',body:png});
    if(path==='/api/projects/'+projectId)return json(project);
    if(path.endsWith('/stats'))return json({total_assets:21-removed.size,annotated_count:0,reviewed_count:0});
    if(path==='/api/assets/selection'){reads++;return json({asset_ids:rows.filter(row=>!removed.has(row.id)).map(row=>row.id),total:21-removed.size});}
    if(path==='/api/assets'){const items=rows.filter(row=>!removed.has(row.id)),p=Number(url.searchParams.get('page')||1),size=Number(url.searchParams.get('page_size')||20);return json({items:items.slice((p-1)*size,p*size),total:items.length,page:p,page_size:size});}
    if(path==='/api/model-services')return json([]);
    if(path==='/api/annotations')return json({items:[],total:0});
    if(path.endsWith('/work/jobs'))return json([]);
    if(path.endsWith('/matches'))return json([{id:'query',query:{text:'人'},version:1,space:{revision:1},hits:rows.slice(0,2).filter(row=>!removed.has(row.id)).map(row=>({...row,asset_id:row.id,score:.9,judgment:'unknown'}))}]);
    return json({detail:'Unexpected mock path '+path},404);
  });
  await page.goto('http://localhost:3000/login');await page.evaluate(()=>{localStorage.clear();localStorage.setItem('token','isolated');localStorage.setItem('user',JSON.stringify({id:1,tenant_id:1,username:'验收',role:'admin'}));});
  for(const path of ['/annotations?project_id='+projectId,'/annotate/project/'+projectId,'/projects/'+projectId+'/images']){
    removed=new Set();await page.goto('http://localhost:3000'+path);await page.getByRole('row').filter({hasText:'crop-0.png'}).waitFor();
    if(await page.getByRole('checkbox',{checked:true}).count())throw Error('Fixture must have zero selected rows');
    const all=page.getByRole('button',{name:/^删除全部/});await all.click();let modal=page.getByRole('dialog',{name:'删除全部图片？'});await modal.getByText('共 21 张图片，将删除其项目记录及标注。',{exact:true}).waitFor();
    const before=batches.length;await modal.getByRole('button',{name:/^取\s*消$/}).click();if(batches.length!==before)throw Error('Cancel deleted assets');
    await all.click();modal=page.getByRole('dialog',{name:'删除全部图片？'});await modal.getByText('共 21 张图片，将删除其项目记录及标注。',{exact:true}).waitFor();
    if(path.startsWith('/annotate/project/')){fail=true;await modal.getByRole('button',{name:'确认删除全部图片',exact:true}).click();await modal.getByText('批量删除失败，请重试',{exact:true}).waitFor();await page.setViewportSize({width:390,height:844});await page.waitForFunction(()=>document.documentElement.scrollWidth<=innerWidth);await page.screenshot({path:'output/playwright/delete-all-mobile.png'});}
    await modal.getByRole('button',{name:'确认删除全部图片',exact:true}).click();await modal.waitFor({state:'hidden'});
    if(batches.at(-1).length!==21||removed.size!==21)throw Error('Delete all was limited to current page');
    await page.getByRole('row').filter({hasText:'crop-0.png'}).waitFor({state:'hidden'});await page.setViewportSize({width:1440,height:1000});checks.push('No-selection delete all 21 rows: '+path.split('?')[0]);
  }
  removed=new Set();await page.goto('http://localhost:3000/annotate/project/'+projectId);await page.getByRole('radiogroup',{name:'标注方式'}).getByText('文搜图标注',{exact:true}).click();
  await page.getByRole('combobox',{name:'历史匹配查询'}).click();await page.locator('.ant-select-dropdown:visible').getByText('人 · query',{exact:true}).click();
  await page.getByRole('button',{name:'删除本次全部结果',exact:true}).click();const modal=page.getByRole('dialog',{name:'删除全部图片？'});await modal.getByText('共 2 张图片，将删除其项目记录及标注。',{exact:true}).waitFor();
  await modal.getByRole('button',{name:'确认删除全部图片',exact:true}).click();await modal.waitFor({state:'hidden'});if(batches.at(-1).length!==2)throw Error('Result range widened');checks.push('All matching results delete without selection');
  if(errors.length)throw Error(errors.join('\n'));return {checks,selectionReads:reads,pageErrors:errors,scope:'All APIs mocked; no business writes'};
}
