async page=>{
  await page.unrouteAll();await page.emulateMedia({reducedMotion:'no-preference'});page.setDefaultTimeout(10000);
  const projectId='33333333-3333-4333-8333-333333333333',cropId='bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',originalId='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
  const png=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aC+sAAAAASUVORK5CYII=','base64');
  const project={id:projectId,project_name:'裁剪删除验收',task_type:'vector',label_schema:{annotation_scope:'crops'},total_assets:2,annotated_count:1,reviewed_count:1};
  const original={id:originalId,project_id:projectId,original_name:'source.png',source_type:'upload',annotation_status:'pending'};
  const crop={id:cropId,project_id:projectId,original_name:'private-crop.png',source_type:'model_crop',annotation_status:'reviewed'};
  let removed=false,failDelete=false;const deletes=[],checks=[],errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  await page.route(/^http:\/\/localhost:3000\/api\//,async route=>{
    const req=route.request(),url=new URL(req.url()),path=url.pathname;const json=(data,status=200)=>route.fulfill({status,json:data});
    if(req.method()==='DELETE'){deletes.push(path);if(path!=='/api/assets/'+cropId)throw Error('Attempt to delete original');if(failDelete){failDelete=false;return json({detail:'删除测试失败，请重试'},409);}removed=true;return route.fulfill({status:204});}
    if(path.endsWith('/content'))return route.fulfill({contentType:'image/png',body:png});
    if(path==='/api/projects/'+projectId)return json({...project,total_assets:removed?1:2});
    if(path.endsWith('/stats'))return json({total_assets:removed?0:1,annotated_count:removed?0:1,reviewed_count:removed?0:1});
    if(path==='/api/assets'){let items=url.searchParams.get('scope')==='annotation'?[crop]:[original,crop];if(removed)items=items.filter(row=>row.id!==cropId);return json({items,total:items.length,page:1,page_size:20});}
    if(path==='/api/model-services')return json([{id:'embed',name:'WEMM',kind:'embedding',revision:1}]);
    if(path==='/api/annotations')return json({items:[],total:0});
    if(path.endsWith('/work/jobs'))return json([]);
    if(path.endsWith('/matches'))return json([{id:'query',query:{text:'黑衣服'},version:1,space:{revision:1},hits:removed?[]:[{asset_id:cropId,original_name:crop.original_name,source_type:'model_crop',score:.9,judgment:'correct'}]}]);
    return json({detail:'Unexpected path '+path},404);
  });
  await page.goto('http://localhost:3000/login');await page.evaluate(()=>{localStorage.clear();localStorage.setItem('token','isolated');localStorage.setItem('user',JSON.stringify({id:1,tenant_id:1,username:'隔离验收',role:'admin'}));});
  const open=async path=>{removed=false;await page.goto('http://localhost:3000'+path);};
  const confirm=async()=>{
    await page.getByRole('button',{name:'删除裁剪图 private-crop.png',exact:true}).click();
    const modal=page.getByRole('dialog',{name:'删除这张裁剪图？'});await modal.getByText(/原图/).waitFor();
    await modal.getByRole('button',{name:'确认删除裁剪图',exact:true}).click();await modal.waitFor({state:'hidden'});
    await page.getByRole('button',{name:'删除裁剪图 private-crop.png',exact:true}).waitFor({state:'hidden'});
  };
  await open('/annotate/project/'+projectId);
  await page.getByRole('button',{name:'删除裁剪图 private-crop.png',exact:true}).waitFor();
  await page.getByRole('button',{name:'删除裁剪图 private-crop.png',exact:true}).click();
  let modal=page.getByRole('dialog',{name:'删除这张裁剪图？'});await modal.getByRole('button',{name:/^取\s*消$/}).click();
  if(deletes.length)throw Error('Cancel issued a deletion');
  await confirm();checks.push('Description list supports confirmed crop deletion; cancellation sends no request');
  await open('/annotations?project_id='+projectId);await confirm();checks.push('Preparation crop rows can be deleted and disappear from selection');
  await open('/projects/'+projectId+'/images');await confirm();await page.getByRole('row').filter({hasText:'source.png'}).waitFor();checks.push('Project images delete crop while retaining original');
  await open('/annotate/project/'+projectId);await page.getByRole('radiogroup',{name:'标注方式'}).getByText('文搜图标注',{exact:true}).click();
  await page.getByRole('combobox',{name:'历史匹配查询'}).click();await page.locator('.ant-select-dropdown:visible').getByText('黑衣服 · query',{exact:true}).click();
  await confirm();checks.push('Matching result crop can be deleted without retaining a visible stale hit');
  await open('/annotate/project/'+projectId);failDelete=true;
  await page.getByRole('button',{name:'删除裁剪图 private-crop.png',exact:true}).click();modal=page.getByRole('dialog',{name:'删除这张裁剪图？'});
  await modal.getByRole('button',{name:'确认删除裁剪图',exact:true}).click();await modal.getByText('删除测试失败，请重试',{exact:true}).waitFor();
  await page.setViewportSize({width:390,height:844});await page.waitForFunction(()=>document.documentElement.scrollWidth<=innerWidth);
  await page.screenshot({path:'output/playwright/crop-delete-mobile.png'});
  await modal.getByRole('button',{name:'确认删除裁剪图',exact:true}).click();await modal.waitFor({state:'hidden'});checks.push('Deletion error preserves confirmation for retry at 390px');
  if(errors.length)throw Error(errors.join('\n'));
  return {checks,deletes:deletes.length,pageErrors:errors,scope:'Mock APIs only; original deletion rejected by fixture'};
}
