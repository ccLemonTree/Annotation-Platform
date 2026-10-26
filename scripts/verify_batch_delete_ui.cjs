async page=>{
  await page.unrouteAll();await page.emulateMedia({reducedMotion:'no-preference'});page.setDefaultTimeout(10000);
  const projectId='33333333-3333-4333-8333-333333333333';
  const rows=Array.from({length:21},(_,i)=>({id:`aaaaaaaa-aaaa-4aaa-8aaa-${String(i).padStart(12,'0')}`,project_id:projectId,original_name:`crop-${i}.png`,source_type:'model_crop',annotation_status:'pending'}));
  const project={id:projectId,project_name:'批量删除验收',task_type:'vector',label_schema:{annotation_scope:'crops'},total_assets:21,annotated_count:0,reviewed_count:0};
  const png=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC0lEQVR42mP8/x8AAwMCAO+aC+sAAAAASUVORK5CYII=','base64');
  let removed=new Set(),fail=false;const batches=[],errors=[],checks=[];
  page.on('pageerror',error=>errors.push(error.message));
  await page.route(/^http:\/\/localhost:3000\/api\//,route=>{
    const req=route.request(),url=new URL(req.url()),path=url.pathname;const json=(value,status=200)=>route.fulfill({json:value,status});
    if(path==='/api/batch/assets/delete'){
      const ids=req.postDataJSON();batches.push(ids);if(ids.some(id=>!rows.some(row=>row.id===id)))throw Error('Out-of-scope deletion');
      if(fail){fail=false;return json({detail:'批量删除失败，请重试'},409);}ids.forEach(id=>removed.add(id));return json({success:true,affected_count:ids.length});
    }
    if(req.method()!=='GET')throw Error('Unexpected mutation '+req.method()+' '+path);
    if(path.endsWith('/content'))return route.fulfill({contentType:'image/png',body:png});
    if(path==='/api/projects/'+projectId)return json({...project,total_assets:21-removed.size});
    if(path.endsWith('/stats'))return json({total_assets:21-removed.size,annotated_count:0,reviewed_count:0});
    if(path==='/api/assets'){const items=rows.filter(row=>!removed.has(row.id));const p=Number(url.searchParams.get('page')||1),size=Number(url.searchParams.get('page_size')||20);return json({items:items.slice((p-1)*size,p*size),total:items.length,page:p,page_size:size});}
    if(path==='/api/model-services')return json([{id:'embedding',name:'WEMM',kind:'embedding',revision:1}]);
    if(path==='/api/annotations')return json({items:[],total:0});
    if(path.endsWith('/work/jobs'))return json([]);
    if(path.endsWith('/matches'))return json([{id:'query',query:{text:'人'},version:1,space:{revision:1},hits:rows.slice(0,2).filter(row=>!removed.has(row.id)).map(row=>({...row,asset_id:row.id,score:.9,judgment:'unknown'}))}]);
    return json({detail:'Unexpected mock path '+path},404);
  });
  await page.goto('http://localhost:3000/login');await page.evaluate(()=>{localStorage.clear();localStorage.setItem('token','isolated');localStorage.setItem('user',JSON.stringify({id:1,tenant_id:1,username:'验收',role:'admin'}));});
  const bulk=()=>page.getByRole('button',{name:/^删除选中图片/});
  const confirm=async()=>{await bulk().click();const modal=page.getByRole('dialog',{name:'批量删除选中图片？'});await modal.getByRole('button',{name:'确认批量删除',exact:true}).click();await modal.waitFor({state:'hidden'});};
  for(const path of ['/annotations?project_id='+projectId,'/annotate/project/'+projectId,'/projects/'+projectId+'/images']){
    removed=new Set();await page.goto('http://localhost:3000'+path);
    await page.getByRole('row').filter({hasText:'crop-0.png'}).getByRole('checkbox').check();
    const before=batches.length;await bulk().click();await page.getByRole('dialog',{name:'批量删除选中图片？'}).getByRole('button',{name:/^取\s*消$/}).click();if(batches.length!==before)throw Error('Cancel deleted assets');
    await page.locator('.ant-pagination-item-2').first().click();await page.getByRole('row').filter({hasText:'crop-20.png'}).getByRole('checkbox').check();
    await confirm();if(batches.at(-1).length!==2||!batches.at(-1).includes(rows[0].id)||!batches.at(-1).includes(rows[20].id))throw Error('Cross-page selection lost');
    await page.getByRole('row').filter({hasText:'crop-1.png'}).waitFor();checks.push('Cross-page batch deletion and cancellation: '+path.split('?')[0]);
  }
  removed=new Set();await page.goto('http://localhost:3000/annotate/project/'+projectId);await page.getByRole('radiogroup',{name:'标注方式'}).getByText('文搜图标注',{exact:true}).click();
  await page.getByRole('combobox',{name:'历史匹配查询'}).click();await page.locator('.ant-select-dropdown:visible').getByText('人 · query',{exact:true}).click();
  await page.getByRole('checkbox',{name:'全选本次结果',exact:true}).check();fail=true;await bulk().click();const modal=page.getByRole('dialog',{name:'批量删除选中图片？'});
  await modal.getByRole('button',{name:'确认批量删除',exact:true}).click();await modal.getByText('批量删除失败，请重试',{exact:true}).waitFor();
  await page.setViewportSize({width:390,height:844});await page.waitForFunction(()=>document.documentElement.scrollWidth<=innerWidth);await page.screenshot({path:'output/playwright/batch-delete-mobile.png'});
  await modal.getByRole('button',{name:'确认批量删除',exact:true}).click();await modal.waitFor({state:'hidden'});if(batches.at(-1).length!==2)throw Error('Result selection lost');
  await page.getByText('当前查询：人 · 模型 r1 · 0 个结果',{exact:true}).waitFor();checks.push('Matching select-all, failed batch retry, 390px and stale hit removal');
  if(errors.length)throw Error(errors.join('\n'));return {checks,batchRequests:batches.length,pageErrors:errors,scope:'All APIs mocked; no business writes'};
}
