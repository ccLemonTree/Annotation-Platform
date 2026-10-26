async page=>{
  await page.unrouteAll();await page.emulateMedia({reducedMotion:'no-preference'});page.setDefaultTimeout(15000);
  await page.addInitScript({path:'F:/hzxf/数据管理平台/backend/backend/.backups/crop-delete/browser-auth.js'});
  const writes=[],errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.goto('http://localhost:3000/annotations');const fixture=await page.evaluate(()=>window.__cropDelete);
  await page.route(/^http:\/\/localhost:3000\/api\//,route=>{
    const req=route.request(),path=new URL(req.url()).pathname;
    if(req.method()!=='GET'){
      if(req.method()!=='DELETE'||path!=='/api/assets/'+fixture.crop_id)throw Error('Write outside owned crop fixture');
      writes.push(path);
    }
    return route.continue();
  });
  await page.goto('http://localhost:3000/annotate/project/'+fixture.project_id);
  const button=page.getByRole('button',{name:/^删除裁剪图 /});await button.waitFor();await button.click();
  const modal=page.getByRole('dialog',{name:'删除这张裁剪图？'});await page.setViewportSize({width:390,height:844});
  await page.waitForFunction(()=>document.documentElement.scrollWidth<=innerWidth);await page.screenshot({path:'output/playwright/crop-delete-live-mobile.png'});
  await modal.getByRole('button',{name:/^取\s*消$/}).click();if(writes.length)throw Error('Cancellation deleted fixture');
  await button.click();await modal.getByRole('button',{name:'确认删除裁剪图',exact:true}).click();await modal.waitFor({state:'hidden'});await button.waitFor({state:'hidden'});
  const result=await page.evaluate(async fixture=>{
    const headers={Authorization:'Bearer '+localStorage.getItem('token')};
    const crop=await fetch('/api/assets/'+fixture.crop_id,{headers}),source=await fetch('/api/assets/'+fixture.source_id,{headers});
    const history=await (await fetch('/api/projects/'+fixture.project_id+'/matches',{headers})).json();
    const stats=await (await fetch('/api/projects/'+fixture.project_id+'/stats',{headers})).json();
    if(crop.status!==404||source.status!==200||history[0].hits.length||stats.total_assets!==1||stats.annotated_count||stats.reviewed_count)throw Error('Deletion did not update live data correctly');
    return {cropStatus:crop.status,sourceStatus:source.status,remainingAssets:stats.total_assets};
  },fixture);
  if(writes.length!==1||errors.length)throw Error(JSON.stringify({writes,errors}));
  return {result,deletions:1,scope:'Only owned temporary crop deleted',pageErrors:errors};
}
