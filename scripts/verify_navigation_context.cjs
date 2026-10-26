async page => {
  await page.unrouteAll();page.setDefaultTimeout(8000);
  const pid='33333333-3333-4333-8333-333333333333',aid='55555555-5555-4555-8555-555555555555',next='55555555-5555-4555-8555-555555555556';
  const project={id:pid,project_name:'导航验收',task_type:'caption',label_schema:{labels:[]},total_assets:2,annotated_count:0,reviewed_count:0};
  await page.route(/^http:\/\/localhost:3000\/api\//,route=>{
    const path=new URL(route.request().url()).pathname;
    const json=body=>route.fulfill({json:body});
    if(route.request().method()!=='GET')throw Error('Unexpected write');
    if(path==='/api/auth/me')return json({id:1,tenant_id:1,role:'admin',username:'验收'});
    if(path==='/api/projects/'+pid)return json(project);
    if(path.endsWith('/neighbors'))return json({previous_id:null,next_id:next});
    if(path==='/api/assets/'+aid||path==='/api/assets/'+next)return json({id:path.split('/').at(-1),project_id:pid,original_name:'test.png',source_type:'model_crop',width:50,height:100});
    if(path==='/api/annotations')return json({items:[],total:0});
    if(path.endsWith('/content'))return route.fulfill({contentType:'image/png',body:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aC+sAAAAASUVORK5CYII=','base64')});
    return json({items:[],total:0});
  });
  await page.goto('http://localhost:3000/login');await page.evaluate(()=>{localStorage.setItem('token','test');localStorage.setItem('user',JSON.stringify({role:'admin',username:'验收'}));});
  await page.goto(`http://localhost:3000/annotate/${aid}?from=review&project_id=${pid}&review_status=pending&page=2`);
  await page.getByRole('link',{name:'下一张',exact:true}).click();
  if(!page.url().includes('from=review')||!page.url().includes('page=2'))throw Error('下一张丢失审核来源及页码');
  await page.getByRole('link',{name:'返回审核列表',exact:true}).click();
  if(!page.url().includes('review_status=pending')||!page.url().includes('page=2')||page.url().includes('from=review'))throw Error('返回未恢复审核筛选');
  return {checks:['单图下一张保留审核来源','返回审核列表恢复状态与页码'],writes:0};
}
