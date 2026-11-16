async page=>{
  page.setDefaultTimeout(12000);
  const existing={id:'11111111-1111-4111-8111-111111111111',project_name:'历史描述项目',task_type:'caption',label_schema:{labels:[]},status:'active',total_assets:0,annotated_count:0,reviewed_count:0,created_at:'2026-11-02T00:00:00Z'};
  const createdId='22222222-2222-4222-8222-222222222222',projects=[existing],writes=[],downloads=[];
  let holdProjects=true,releaseProjects;let projectsGate=new Promise(resolve=>{releaseProjects=resolve;});
  const api=/^http:\/\/localhost:3000\/api\//;
  await page.addInitScript(()=>{if(location.protocol==='http:')localStorage.setItem('token','isolated-smarteye-project-token');});
  await page.route(api,async route=>{
    const request=route.request(),url=new URL(request.url()),path=url.pathname,method=request.method();let data;
    if(method!=='GET')writes.push({path,body:request.postDataJSON()});
    if(path==='/api/auth/me')data={id:1,username:'fixture',role:'admin',tenant_id:1,is_active:true};
    else if(path==='/api/projects'){
      if(method==='POST'){const body=request.postDataJSON();data={...existing,...body,id:body.task_type==='caption'?'44444444-4444-4444-8444-444444444444':body.project_name.startsWith('设备')?'33333333-3333-4333-8333-333333333333':createdId};projects.push(data);holdProjects=false;}
      else{const snapshot=[...projects];if(holdProjects)await projectsGate;data={items:snapshot,total:snapshot.length,page:1,page_size:100};}
    }else if(path.startsWith('/api/projects/'))data=projects.find(project=>path.includes(project.id));
    else if(path==='/api/images/collections')data=[];
    else if(path==='/api/model-services')data=[];
    else if(path==='/api/automation/jobs'||path==='/api/automation/review-queue')data={items:[],total:0,page:1,page_size:20};
    else if(path==='/api/smarteye/platform-list')return route.fulfill({json:{code:0,data:[{value:'fixture',label:'隔离查询平台',alarmTypes:{人:'1'}}]}});
    else if(path==='/api/smarteye/platforms')return route.fulfill({json:{code:0,data:{records:[{id:'query-fixture',platform:'隔离查询平台',device:'保留的查询设备',deviceId:'camera-1',picUrl:'',targetType:'人'}],total:1,pages:1}}});
    else if(path==='/api/smarteye/download'){downloads.push(await request.allHeaders());return route.fulfill({json:{code:1,message:'隔离测试：不执行真实下载'}});}
    else if(path==='/api/smarteye/devices')return route.fulfill({json:{code:0,data:{items:[],total:0}}});
    else if(path==='/api/smarteye/device-scenes/config')return route.fulfill({json:{code:0,data:{scenes:[],classification_enabled:false}}});
    else if(path.startsWith('/api/smarteye/'))return route.fulfill({json:{code:0,data:[]}});
    else data=[];
    await route.fulfill({json:data});
  });
  const releaseInitialProjects=async()=>{
    const response=page.waitForResponse(response=>new URL(response.url()).pathname==='/api/projects'&&response.request().method()==='GET');
    releaseProjects();await response;await page.evaluate(()=>new Promise(resolve=>setTimeout(resolve,100)));
  };
  try{
    await page.goto('http://localhost:3000/detection-query');
    await page.evaluate(()=>{window.smartEyeMessages=[];window.addEventListener('message',event=>{if(event.data?.type==='smarteye:open-automation')window.smartEyeMessages.push(event.data);});});
    const frame=page.frameLocator('iframe[title="SmartEye 数据拉取"]');
    await frame.getByRole('button',{name:'选择查询平台',exact:true}).click();await frame.getByRole('checkbox',{name:'隔离查询平台',exact:true}).check();await frame.getByRole('button',{name:'选择查询平台',exact:true}).click();
    await frame.getByRole('button',{name:/查询$/}).click();await frame.getByText('camera-1',{exact:true}).waitFor();
    await frame.getByRole('button',{name:'新建项目',exact:true}).click();
    const modal=frame.getByRole('dialog',{name:'新建项目',exact:true});await modal.waitFor();
    await modal.getByRole('textbox',{name:'项目名称',exact:true}).fill('拉取中新建检测项目');
    await modal.getByRole('button',{name:'创建项目',exact:true}).click();
    await modal.getByText('请至少设置一个类别',{exact:true}).waitFor();
    if(writes.some(write=>write.path==='/api/projects'))throw Error('缺少检测类别仍创建项目');
    const labels=modal.getByRole('combobox',{name:'项目标注类别',exact:true});await labels.fill('人');await labels.press('Enter');
    await modal.getByRole('button',{name:'创建项目',exact:true}).click();await modal.waitFor({state:'hidden'});
    const create=writes.find(write=>write.path==='/api/projects');
    if(create.body.task_type!=='detection'||JSON.stringify(create.body.label_schema.labels)!=='["人"]')throw Error('项目创建没有复用检测类别规则');
    await frame.getByText('拉取中新建检测项目',{exact:true}).first().waitFor();
    await releaseInitialProjects();
    if(!await frame.getByRole('button',{name:'预标注与AI审核',exact:true}).isEnabled())throw Error('迟到初始列表覆盖了拉取页新建项目');
    if(new URL(page.url()).pathname!=='/detection-query')throw Error('创建项目跳离数据拉取');
    await frame.getByText('camera-1',{exact:true}).waitFor();
    await frame.getByRole('button',{name:/下载所有图片/}).click();await frame.getByText(/隔离测试：不执行真实下载/).waitFor();
    if(downloads[0]['x-smarteye-purpose']!=='annotation'||downloads[0]['x-smarteye-project']!==createdId)throw Error('新建成功后没有选中新项目/标注用途');
    await frame.getByRole('button',{name:'预标注与AI审核',exact:true}).click();
    const drawer=page.getByRole('dialog').filter({has:page.getByRole('button',{name:'创建任务',exact:true})});await drawer.waitFor();
    if(await page.evaluate(()=>window.smartEyeMessages.at(-1)?.projectId)!==createdId)throw Error('父页消息未绑定新建项目');
    if(new URL(page.url()).pathname!=='/detection-query')throw Error('预标注入口跳离数据拉取');
    await page.locator('.ant-drawer-close').click();await drawer.waitFor({state:'hidden'});
    await frame.getByText('camera-1',{exact:true}).waitFor();
    await page.evaluate(async projectId=>{window.postMessage({type:'smarteye:open-automation',projectId},location.origin);await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));},createdId);
    if(await drawer.count())throw Error('主窗口伪造postMessage打开了预标注Drawer');
    const selection=frame.getByRole('combobox',{name:'标注项目',exact:true});await selection.fill(existing.project_name);await selection.press('ArrowDown');await selection.press('Enter');
    if(!await frame.getByRole('button',{name:'预标注与AI审核',exact:true}).isDisabled())throw Error('描述项目错误进入检测预标注');
    await page.setViewportSize({width:390,height:844});await page.waitForFunction(()=>getComputedStyle(document.querySelector('.platform-main')).marginLeft==='0px');
    if(await frame.locator('html').evaluate(element=>element.scrollWidth>element.clientWidth+1))throw Error('拉取项目操作手机端溢出');
    await page.screenshot({path:'output/playwright/smarteye-project-actions-mobile.png',fullPage:true,animations:'disabled'});
    holdProjects=true;projectsGate=new Promise(resolve=>{releaseProjects=resolve;});
    await page.setViewportSize({width:1440,height:1000});await page.goto(`http://localhost:3000/detection-query?project_id=${existing.id}`);
    await frame.getByRole('heading',{name:'设备抓图与场景',exact:true}).waitFor();
    await frame.getByRole('button',{name:'新建项目',exact:true}).click();
    const deviceModal=frame.getByRole('dialog',{name:'新建项目',exact:true});await deviceModal.getByRole('textbox',{name:'项目名称',exact:true}).fill('设备页新建检测项目');
    const deviceLabels=deviceModal.getByRole('combobox',{name:'项目标注类别',exact:true});await deviceLabels.fill('人');await deviceLabels.press('Enter');await deviceModal.getByRole('button',{name:'创建项目',exact:true}).click();await deviceModal.waitFor({state:'hidden'});
    await frame.getByText('设备页新建检测项目',{exact:true}).first().waitFor();
    await releaseInitialProjects();
    if(!await frame.getByRole('button',{name:'预标注与AI审核',exact:true}).isEnabled())throw Error('迟到初始列表覆盖了设备页新建项目');
    await frame.getByRole('button',{name:'预标注与AI审核',exact:true}).click();await drawer.waitFor();
    await page.locator('.ant-drawer-close').click();await drawer.waitFor({state:'hidden'});
    if(writes.some(write=>write.path.includes('/device-capture/schedules')))throw Error('新建项目静默修改了已有调度');
    await frame.getByRole('button',{name:'新建项目',exact:true}).click();
    const captionModal=frame.getByRole('dialog',{name:'新建项目',exact:true});await captionModal.getByRole('textbox',{name:'项目名称',exact:true}).fill('拉取描述项目');
    await captionModal.getByRole('combobox',{name:/任务类型/}).click();await frame.locator('.ant-select-dropdown:visible .ant-select-item-option-content').getByText('大模型描述标注',{exact:true}).click();
    await captionModal.getByRole('button',{name:'创建项目',exact:true}).click();await captionModal.waitFor({state:'hidden'});
    const captionCreate=writes.filter(write=>write.path==='/api/projects').at(-1);
    if(captionCreate.body.task_type!=='caption'||captionCreate.body.label_schema.labels.length||captionCreate.body.label_schema.classification_mode!=='single')throw Error('描述项目未允许空类别或缺少分类模式');
    if(!await frame.getByRole('button',{name:'预标注与AI审核',exact:true}).isDisabled())throw Error('新建描述项目仍允许检测预标注');
    await page.screenshot({path:'output/playwright/smarteye-device-project-actions-desktop.png',fullPage:true,animations:'disabled'});
    await page.goto('http://localhost:3000/smarteye/index.html?project_id=33333333-3333-4333-8333-333333333333');
    await page.getByRole('button',{name:'预标注与AI审核',exact:true}).click();
    await page.waitForURL(/\/detection-query\?project_id=33333333-3333-4333-8333-333333333333&automation=1$/);await drawer.waitFor();
    return {checks:['数据拉取中创建项目不跳页','检测类别必填/描述允许空类别','创建后自动选项目及标注用途','预标注同源消息保持查询结果','主窗口伪造消息不能打开预标注','设备页新建不修改已有调度','非检测项目禁用检测预标注','独立页面回数据拉取入口打开预标注','手机布局'],scope:'全部根/api隔离，无业务项目/下载/批量任务写入'};
  }catch(error){await page.screenshot({path:'output/playwright/smarteye-project-actions-failure.png',fullPage:true,animations:'disabled'});throw error;}
  finally{releaseProjects();await page.goto('about:blank');await page.unroute(api);await page.setViewportSize({width:1440,height:1000});}
}
