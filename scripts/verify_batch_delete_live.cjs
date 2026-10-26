async page=>{
  await page.unrouteAll();await page.emulateMedia({reducedMotion:'no-preference'});page.setDefaultTimeout(15000);
  await page.addInitScript({path:'F:/hzxf/数据管理平台/backend/backend/.backups/batch-delete/browser-auth.js'});
  const requests=[],errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.goto('http://localhost:3000/annotations');const fixture=await page.evaluate(()=>window.__batchDelete);
  await page.route(/^http:\/\/localhost:3000\/api\//,route=>{
    const req=route.request();if(req.method()!=='GET'){
      const path=new URL(req.url()).pathname;
      if(req.method()!=='POST'||path!=='/api/batch/assets/delete'||JSON.stringify([...req.postDataJSON()].sort())!==JSON.stringify([...fixture.crop_ids].sort()))throw Error('Write outside owned selection');
      requests.push(req.postDataJSON());
    }return route.continue();
  });
  await page.goto('http://localhost:3000/annotate/project/'+fixture.project_id);
  await page.getByRole('row').count();await page.getByRole('checkbox').first().check();
  const button=page.getByRole('button',{name:'删除选中图片（2）',exact:true});await button.waitFor();await button.click();
  const modal=page.getByRole('dialog',{name:'批量删除选中图片？'});await modal.waitFor();await page.setViewportSize({width:390,height:844});await page.waitForFunction(()=>document.documentElement.scrollWidth<=innerWidth);
  await page.screenshot({path:'output/playwright/batch-delete-live-mobile.png'});
  await modal.getByRole('button',{name:/^取\s*消$/}).click();if(requests.length)throw Error('Cancel sent request');
  await button.click();await modal.getByRole('button',{name:'确认批量删除',exact:true}).click();await modal.waitFor({state:'hidden'});
  await page.getByRole('button',{name:'删除选中图片（0）',exact:true}).waitFor();
  const result=await page.evaluate(async fixture=>{
    const headers={Authorization:'Bearer '+localStorage.getItem('token')};
    const states=await Promise.all(fixture.crop_ids.map(async id=>(await fetch('/api/assets/'+id,{headers})).status));
    const source=(await fetch('/api/assets/'+fixture.source_id,{headers})).status;
    const stats=await (await fetch('/api/projects/'+fixture.project_id+'/stats',{headers})).json();
    const history=await (await fetch('/api/projects/'+fixture.project_id+'/matches',{headers})).json();
    if(states.some(status=>status!==404)||source!==200||stats.total_assets!==1||stats.annotated_count||stats.reviewed_count||history[0].hits.length)throw Error('Batch deletion inconsistent');
    return {cropStatuses:states,sourceStatus:source,remainingAssets:stats.total_assets};
  },fixture);
  if(requests.length!==1||errors.length)throw Error(JSON.stringify({requests,errors}));
  return {batchRequests:1,deleted:2,result,pageErrors:errors,scope:'Owned temporary project only'};
}
