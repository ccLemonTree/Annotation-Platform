async page=>{
  await page.unrouteAll({behavior:'ignoreErrors'});page.setDefaultTimeout(7000);
  const base='http://127.0.0.1:3000',id='11111111-1111-4111-8111-111111111111',assetId='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
  const project={id,tenant_id:1,project_name:'分割测试',task_type:'segmentation',status:'active',label_schema:{labels:[]},total_assets:0,annotated_count:0,reviewed_count:0,created_at:'2026-11-01T00:00:00Z'};
  let requests=[],unexpected=[],errors=[];await page.addInitScript(()=>localStorage.setItem('token','isolated-project-guidance'));
  page.on('pageerror',error=>errors.push(error.message));
  await page.route(/^https?:\/\/[^/]+\/api(?:\/|\?|$)/,async route=>{
    const req=route.request(),url=new URL(req.url()),path=url.pathname,method=req.method();requests.push([method,path]);
    if(url.origin!==base){unexpected.push(req.url());return route.abort();}
    if(method==='POST'&&path==='/api/projects')return route.fulfill({status:503,json:{detail:'模拟创建失败'}});
    if(method!=='GET'){unexpected.push(method+' '+path);return route.fulfill({status:500,json:{detail:'unexpected'}});}
    if(path==='/api/auth/me')return route.fulfill({json:{id:1,tenant_id:1,username:'fixture',role:'admin'}});
    if(path==='/api/projects')return route.fulfill({json:{items:[project],total:1}});
    if(path===`/api/projects/${id}`)return route.fulfill({json:project});
    if(path===`/api/projects/${id}/stats`)return route.fulfill({json:{total_assets:project.total_assets,annotated_count:0,reviewed_count:0}});
    const asset={id:assetId,project_id:id,original_name:'测试图片.jpg',width:640,height:480,file_size:10,source_type:'upload',created_at:'2026-11-01T00:00:00Z'};
    if(path===`/api/assets/${assetId}/content`)return route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="64" height="48"/>'});
    if(path===`/api/assets/${assetId}`)return route.fulfill({json:asset});
    if(path==='/api/annotations')return route.fulfill({json:{items:project.task_type==='segmentation'&&project.total_assets?[{id:'ann',asset_id:assetId,version:1,is_latest:true,review_status:'pending',annotation_data:{boxes:[]},updated_at:'2026-11-01T00:00:00Z'}]:[],total:project.task_type==='segmentation'&&project.total_assets?1:0}});
    if(path==='/api/assets')return route.fulfill({json:{items:project.total_assets?[asset]:[],total:project.total_assets?1:0},headers:{'X-Asset-Crop-Depth-Filter':'1'}});
    if(path==='/api/images')return route.fulfill({json:{items:[],total:0}});
    if(path==='/api/images/collections'||path==='/api/assets/collections'||path==='/api/model-services'||path===`/api/projects/${id}/work/jobs`)return route.fulfill({json:[]});
    if(path==='/api/images/ingest-settings')return route.fulfill({json:{classification_enabled:false,base_url:'',model:'',scenes:[],min_long_edge:0}});
    if(path==='/api/model-services/prompt-defaults')return route.fulfill({json:{}});
    unexpected.push(method+' '+path);return route.fulfill({status:500,json:{detail:'unexpected'}});
  });
  const check=(condition,message)=>{if(!condition)throw Error(message);};
  await page.setViewportSize({width:1440,height:1000});await page.goto(base+'/projects');
  await page.getByRole('button',{name:/新建项目/}).click();const modal=page.getByRole('dialog',{name:'新建项目'});
  await modal.getByText('创建后不能更改任务类型',{exact:false}).waitFor();
  for(const [name,help] of [['图像分类','例如区分图片场景'],['大模型描述 / 目标属性分析','例如描述画面'],['向量匹配标注','例如按文字或图片检索']]){
    await modal.getByRole('combobox',{name:'任务类型'}).click();await page.locator('.ant-select-dropdown:visible').getByText(name,{exact:true}).click();await modal.getByText(help,{exact:false}).waitFor();
    await modal.getByText(name==='图像分类'?'暂不支持模型测试评估':'支持标注、导出训练和模型测试评估',{exact:false}).waitFor();
  }
  await modal.getByRole('combobox',{name:'任务类型'}).click();const unavailable=page.locator('.ant-select-dropdown:visible').getByText('图像分割（暂不支持）',{exact:true}).locator('..');
  check((await unavailable.getAttribute('class')).includes('disabled'),'不支持的分割新项目仍可选择');await page.keyboard.press('Escape');
  await modal.getByRole('button',{name:'创建项目'}).click();await modal.getByText('请输入项目名称').waitFor();
  check(!await page.getByText('创建失败',{exact:true}).count(),'本地校验出现创建失败');check(!requests.some(([m,p])=>m==='POST'&&p==='/api/projects'),'本地校验发出请求');
  await modal.getByLabel('项目名称').fill('测试项目');await modal.getByRole('button',{name:'创建项目'}).click();
  await page.getByText('创建失败',{exact:true}).waitFor();check(requests.some(([m,p])=>m==='POST'&&p==='/api/projects'),'实际失败未发送请求');
  await page.screenshot({path:'output/playwright/novice-project-task-types.png',fullPage:false,animations:'disabled'});
  await page.goto(base+`/projects/${id}/settings`);await page.getByRole('button',{name:/编辑名称与类别/}).click();
  const edit=page.getByRole('dialog',{name:'编辑项目'});await edit.getByText('当前仅保留项目与图片管理',{exact:false}).waitFor();
  check(await edit.getByRole('combobox',{name:'任务类型'}).isDisabled(),'旧分割项目允许修改类型');await edit.getByRole('button',{name:/^取\s*消$/}).click();
  await page.goto(base+`/projects/${id}/deliver`);await page.getByText('图像分割项目暂不支持',{exact:false}).waitFor();
  check(!await page.getByRole('button',{name:/导出|创建训练/}).count(),'分割仍显示不可用的导出训练');
  check(!requests.some(([m,p])=>p.startsWith('/api/export')||p.startsWith('/api/training')),'分割访问导出或训练接口');
  await page.screenshot({path:'output/playwright/novice-segmentation-boundary.png',fullPage:false,animations:'disabled'});
  project.total_assets=1;await page.goto(base+`/projects/${id}/images`);
  await page.getByText('分割标注暂不支持',{exact:true}).waitFor();check(!await page.getByRole('link',{name:'标注 / 审核',exact:true}).count(),'分割图片仍可打开不支持的工作台');
  await page.goto(base+`/annotations?project_id=${id}`);await page.getByText('分割标注暂不支持',{exact:true}).waitFor();
  check(!await page.getByRole('link',{name:'打开标注 / 审核',exact:true}).count(),'分割历史仍可打开不支持的工作台');
  project.task_type='detection';project.total_assets=0;await page.goto(base+`/annotations?project_id=${id}`);
  await page.getByText('先添加图片',{exact:false}).first().waitFor();check(await page.getByRole('link',{name:'设置标注类别'}).count()===1,'无设置类别入口');
  check(!await page.getByText('从上方开始或继续标注',{exact:false}).count(),'空类别仍提示从上方开始');
  project.total_assets=3;await page.reload();await page.getByText('先设置标注类别',{exact:false}).first().waitFor();
  project.total_assets=0;project.label_schema.labels=['car'];await page.reload();await page.getByText('当前标注范围暂无图片',{exact:false}).first().waitFor();
  check(!await page.getByText('请从上方开始标注',{exact:false}).count(),'无图时仍提示开始标注');
  project.total_assets=3;project.label_schema.labels=[];await page.goto(base+`/projects/${id}/images`);await page.getByRole('link',{name:'标注 / 审核',exact:true}).waitFor();
  await page.goto(base+`/projects/${id}/images`);await page.getByRole('button',{name:'复用已有数据',exact:true}).click();
  await page.getByText('从最近已完成记录点击“添加到当前项目”',{exact:false}).waitFor();
  await page.setViewportSize({width:390,height:844});await page.getByText('从最近已完成记录点击“添加到当前项目”',{exact:false}).scrollIntoViewIfNeeded();await page.screenshot({path:'output/playwright/novice-yolo-recovery-mobile.png',fullPage:false,animations:'disabled'});
  check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'移动端页面水平溢出');
  if(unexpected.length||errors.length)throw Error([...unexpected,...errors].join('\n'));
  return {createValidation:true,remoteFailure:true,segmentationBoundary:true,emptyProject:true,withImages:true,unknown:unexpected,errors};
}
