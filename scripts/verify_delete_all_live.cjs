async page=>{
  await page.unrouteAll();await page.emulateMedia({reducedMotion:'no-preference'});page.setDefaultTimeout(15000);
  await page.addInitScript({path:'F:/hzxf/数据管理平台/backend/backend/.backups/delete-all/browser-auth.js'});
  const requests=[],errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.goto('http://localhost:3000/annotations');const fixture=await page.evaluate(()=>window.__deleteAll);
  await page.route(/^http:\/\/localhost:3000\/api\//,route=>{
    const req=route.request();if(req.method()!=='GET'){
      const path=new URL(req.url()).pathname;
      if(req.method()!=='POST'||path!=='/api/batch/assets/delete'||JSON.stringify([...req.postDataJSON()].sort())!==JSON.stringify([...fixture.crop_ids].sort()))throw Error('Write outside owned fixture');
      requests.push(req.postDataJSON());
    }return route.continue();
  });
  await page.goto('http://localhost:3000/annotate/project/'+fixture.project_id);
  const all=page.getByRole('button',{name:'删除全部裁剪图',exact:true});await all.waitFor();
  await page.locator('.ant-table-tbody tr.ant-table-row').first().waitFor();
  if(await page.getByRole('checkbox',{checked:true}).count())throw Error('Rows must not be checked');
  const visible=await page.locator('.ant-table-tbody tr.ant-table-row').count();if(visible!==20)throw Error('Expected 20 visible rows');
  await all.click();const modal=page.getByRole('dialog',{name:'删除全部图片？'});
  await modal.getByText('共 21 张图片，将删除其项目记录及标注。',{exact:true}).waitFor();
  await modal.getByRole('button',{name:/^取\s*消$/}).click();if(requests.length)throw Error('Cancel sent request');
  await all.click();await modal.getByText('共 21 张图片，将删除其项目记录及标注。',{exact:true}).waitFor();
  await page.setViewportSize({width:390,height:844});await page.waitForFunction(()=>document.documentElement.scrollWidth<=innerWidth);
  await page.screenshot({path:'output/playwright/delete-all-live-mobile.png'});
  await modal.getByRole('button',{name:'确认删除全部图片',exact:true}).click();await modal.waitFor({state:'hidden'});
  const result=await page.evaluate(async fixture=>{
    const headers={Authorization:'Bearer '+localStorage.getItem('token')};
    const list=await (await fetch('/api/assets?project_id='+fixture.project_id+'&scope=annotation',{headers})).json();
    const source=(await fetch('/api/assets/'+fixture.source_id,{headers})).status;
    const stats=await (await fetch('/api/projects/'+fixture.project_id+'/stats',{headers})).json();
    const history=await (await fetch('/api/projects/'+fixture.project_id+'/matches',{headers})).json();
    if(list.total||source!==200||stats.total_assets!==1||stats.annotated_count||stats.reviewed_count||history[0].hits.length)throw Error('Incomplete delete all');
    return {remainingCrops:list.total,sourceStatus:source,remainingAssets:stats.total_assets};
  },fixture);
  if(requests.length!==1||errors.length)throw Error(JSON.stringify({requests,errors}));
  return {visibleRows:visible,checkedRows:0,deleted:requests[0].length,batchRequests:1,result,pageErrors:errors,scope:'Owned temporary project only'};
}
