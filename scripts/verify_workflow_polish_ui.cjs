async page=>{
  await page.unrouteAll({behavior:'ignoreErrors'});page.setDefaultTimeout(12000);
  const base='http://127.0.0.1:3000',reads=[],writes=[],errors=[];
  const initial={id:'11111111-1111-4111-8111-111111111111',project_name:'设备采集项目',task_type:'detection',status:'active',label_schema:{labels:['人']},total_assets:10,annotated_count:5,reviewed_count:3,created_at:'2026-11-04T00:00:00Z'};
  let projects=[initial];
  const common={enabled:true,revision:2,key:'fixture',name:'在线模型',kind:'detection',protocol:'model_service',base_url:'http://fixture',endpoint:'/detect',model_name:'fixture',labels:['人'],label_mapping:{},timeout_seconds:30,confidence:.3,iou:.5};
  const models=[{...common,id:'online',connection_check:{status:'online',checked_revision:2}},{...common,id:'offline',name:'离线模型',connection_check:{status:'offline',checked_revision:2}},{...common,id:'caption',name:'在线审核模型',kind:'caption',protocol:'openai_chat'}];
  const device={plat_id:'p',device_id:'d',device_name:'学校摄像头',platform_name:'平台一',scene_labels:['学校','高位摄像头'],scene_status:'completed'};
  const api=/^https?:\/\/[^/]+\/api\//;
  const handler=async route=>{
    const req=route.request(),url=new URL(req.url()),p=url.pathname;reads.push(req.url());
    if(req.method()!=='GET')writes.push({path:p,body:req.postDataJSON()});
    let data;
    if(p==='/api/auth/me')data={id:1,tenant_id:1,role:'admin',username:'隔离验收'};
    else if(p==='/api/projects'){
      if(req.method()==='POST'){const body=req.postDataJSON();const created={...initial,...body,id:'22222222-2222-4222-8222-222222222222',total_assets:0,annotated_count:0,reviewed_count:0};projects=[created,...projects];data=created;}
      else data={items:projects,total:projects.length};
    }else if(p==='/api/projects/'+initial.id)data=initial;
    else if(p==='/api/images/collections')data=[{id:'collection',name:'设备采集项目',image_count:1}];
    else if(p==='/api/images/source-platforms')data=['平台一','平台二'];
    else if(p==='/api/images')data={items:[],total:0,page:1,page_size:20};
    else if(p==='/api/model-services')data=models;
    else if(p==='/api/assets/selection')data={asset_ids:['asset-1'],total:1};
    else if(p==='/api/automation/jobs'||p==='/api/automation/review-queue')data={items:[],total:0,page:1,page_size:20};
    else if(p==='/api/validation/sets'||p==='/api/validation/runs')data={items:[],total:0};
    else if(p==='/api/training/catalog')data={};
    else if(p.startsWith('/api/smarteye/')){
      if(p.endsWith('/managed-platforms'))data=[{plat_id:'p',platform_name:'平台一',state:'1',connection_status:'online'}];
      else if(p.endsWith('/devices'))data={items:[device],total:1};
      else if(p.endsWith('/device-scenes/config'))data={classification_enabled:true,scenes:['学校','高位摄像头','低位摄像头'],scene_groups:[{name:'公共区域',scenes:['学校']},{name:'摄像机位',scenes:['高位摄像头','低位摄像头']}]};
      else data=[];
      return route.fulfill({json:{code:0,data}});
    }else data=[];
    return route.fulfill({json:data});
  };
  const choose=async(locator,text)=>{await locator.click();await page.locator('.ant-select-dropdown:visible .ant-select-item-option-content').getByText(text,{exact:true}).click();};
  page.on('pageerror',error=>errors.push(error.message));
  await page.addInitScript(()=>localStorage.setItem('token','isolated-workflow-polish'));
  await page.route(api,handler);
  try{
    await page.setViewportSize({width:1440,height:1000});await page.goto(base+'/projects');
    await page.getByRole('button',{name:/新建项目$/}).click();
    const create=page.getByRole('dialog',{name:'新建项目',exact:true});
    await create.getByPlaceholder('请输入项目名称',{exact:true}).fill('新建后留在项目页');
    await choose(create.getByRole('combobox').first(),'大模型描述 / 目标属性分析');
    await create.getByRole('button',{name:/^确\s*定$/}).click();await create.waitFor({state:'hidden'});
    await page.getByRole('article',{name:'新建后留在项目页',exact:true}).waitFor();await page.waitForLoadState('networkidle');
    if(await page.getByRole('dialog',{name:/上传到项目/}).count())throw Error('新建项目仍自动打开上传');
    const card=page.getByRole('article',{name:initial.project_name,exact:true});
    await card.getByRole('button',{name:'删除项目图片',exact:true}).click();
    const deletion=page.getByRole('dialog',{name:'删除全部图片？'});await deletion.waitFor();
    await deletion.getByRole('button',{name:/^取\s*消$/}).click();
    if(writes.some(write=>write.path.includes('delete')))throw Error('取消删除仍有写入');
    await card.getByRole('button',{name:'预标注与AI审核',exact:true}).click();
    const drawer=page.getByRole('dialog').filter({has:page.getByRole('button',{name:'创建任务',exact:true})});await drawer.getByRole('button',{name:'创建任务',exact:true}).click();
    const form=page.getByRole('dialog',{name:'创建预标注 / AI审核任务'});if(await form.locator('.ant-select-selection-item').filter({hasText:'离线模型'}).count())throw Error('默认选择了离线模型');await form.getByRole('combobox',{name:'检测模型',exact:true}).click();
    const offline=page.locator('.ant-select-dropdown:visible .ant-select-item-option').filter({hasText:'离线模型'});
    await offline.waitFor();if(!await offline.evaluate(element=>element.classList.contains('ant-select-item-option-disabled')))throw Error('离线模型仍可选');
    await page.keyboard.press('Escape');await form.getByRole('button',{name:'关闭创建',exact:true}).click();await page.locator('.ant-drawer-close').click();
    await page.goto(base+'/assets');await choose(page.getByRole('combobox',{name:'图片来源平台'}),'平台二');
    const filtered=page.waitForResponse(response=>response.url().includes('/api/images?')&&new URL(response.url()).searchParams.get('device_name')==='门口');
    await page.getByLabel('图片来源设备',{exact:true}).fill('门口');await page.getByLabel('图片来源设备',{exact:true}).press('Enter');await filtered;
    if(!reads.some(value=>value.includes('platform_name=')&&value.includes('device_name=')))throw Error('来源筛选未组合');
    await page.goto(base+'/projects/'+initial.id+'/capture');const frame=page.frameLocator('iframe');
    await frame.getByRole('combobox',{name:'摄像机位',exact:true}).waitFor();
    const cameraQuery=page.waitForResponse(response=>response.url().includes('/api/smarteye/devices?')&&new URL(response.url()).searchParams.get('camera_position')==='高位摄像头');
    await frame.getByRole('combobox',{name:'摄像机位',exact:true}).click();await frame.locator('.ant-select-dropdown:visible .ant-select-item-option-content').getByText('高位摄像头',{exact:true}).click();await cameraQuery;await page.keyboard.press('Escape');
    if(await frame.getByRole('heading',{name:'同步与分类进度',exact:true}).isVisible())throw Error('进度仍占据设备列表上方');
    await frame.getByRole('button',{name:'同步与分类进度',exact:true}).click();await frame.getByRole('dialog',{name:'同步与分类进度',exact:true}).waitFor();
    await page.screenshot({path:'output/playwright/workflow-polish-progress.png',fullPage:true,animations:'disabled'});
    await page.goto(base+'/validation?project_id='+initial.id+'&task_type=detection');
    await page.getByRole('tab',{name:'数据集评估',exact:true}).waitFor();
    if(!page.url().includes('/model-preview?')||!page.url().includes('project_id='+initial.id))throw Error('验证旧入口未保留项目重定向');
    if(await page.locator('#platform-navigation').getByText('模型验证',{exact:true}).count())throw Error('仍有独立验证菜单');
    await page.getByRole('tab',{name:'快速测试',exact:true}).click();
    await page.setViewportSize({width:390,height:844});
    if(await page.locator('html').evaluate(element=>element.scrollWidth>element.clientWidth+1))throw Error('手机布局溢出');
    if(errors.length)throw Error(errors.join(';'));
    return {checks:['新建不弹上传','项目删除入口及取消无写入','离线模型不可选','图片按平台和设备组合查看','摄像机位独立筛选','进度抽屉','模型验证合并并保留旧链接上下文','390px布局'],writes};
  }catch(error){await page.screenshot({path:'output/playwright/workflow-polish-failure.png',fullPage:true,animations:'disabled'});throw error;}finally{await page.goto('about:blank');await page.unroute(api,handler);await page.setViewportSize({width:1440,height:1000});}
}
