async page=>{
  await page.unrouteAll();page.setDefaultTimeout(12000);
  const pattern=/^http:\/\/192\.168\.1\.90:3000\/api\/smarteye\//;
  const writes=[],errors=[];page.on('pageerror',error=>errors.push(error.message));
  const task={task_id:'fixture-ledger',status:'completed',progress:100,created_at:1792370000,
    message:'完成！预警 6 条，对应成功 4，未匹配 1，失败 1；下载 4 张（含重复）, 裁切 0 张',platform_results:[]};
  await page.route(pattern,async route=>{
    const request=route.request(),path=new URL(request.url()).pathname;
    let data=[];
    if(request.method()==='POST'){
      if(!['/api/smarteye/ledger-watch/jobs','/api/smarteye/ledger-image/scheduler/configure'].includes(path))throw Error('Unexpected mutation '+path);
      writes.push({path,...request.postDataJSON()});data={job_id:'fixture-watch',sched_id:'fixture-schedule'};
    }else if(path.endsWith('/platform-list'))data=[{value:'fixture',label:'同步验收平台',alarmTypes:{火情:'113'}}];
    else if(path.endsWith('/scheduler/status'))data={schedulers:[],all_tasks:[task]};
    else if(!path.endsWith('/ledger-watch/jobs'))throw Error('Unexpected SmartEye read '+path);
    return route.fulfill({json:{code:0,data}});
  });
  try{
    await page.setViewportSize({width:1440,height:1000});await page.goto('http://192.168.1.90:3000/detection-query');
    const frame=page.frameLocator('iframe[title="SmartEye 数据拉取"]');
    await frame.getByRole('menuitem').filter({hasText:'台账下载'}).click();
    await frame.getByText(task.message,{exact:true}).waitFor();
    await frame.getByRole('button',{name:/新建下载任务/}).click();
    let dialog=frame.getByRole('dialog',{name:'新建下载任务',exact:true});
    await dialog.getByRole('textbox',{name:'名称'}).fill('同步定时验收');
    await dialog.getByRole('button',{name:/选择平台/}).click();
    await dialog.getByRole('checkbox',{name:'同步验收平台'}).check();
    await dialog.getByRole('button',{name:/同步验收平台/}).click();
    await dialog.locator('label').filter({hasText:'定时执行（循环下载）'}).locator('div').first().click();
    await dialog.getByText('执行间隔（预警查询范围）',{exact:true}).waitFor();
    await dialog.getByRole('button',{name:'创建并启动',exact:true}).click();await dialog.waitFor({state:'hidden'});
    await frame.getByRole('button',{name:'监听下载',exact:true}).click();
    await frame.getByRole('button',{name:/新建监听任务/}).click();
    dialog=frame.getByRole('dialog',{name:'新建监听任务',exact:true});
    await dialog.getByText('固定 15 秒',{exact:true}).waitFor();
    await dialog.getByRole('textbox',{name:'名称'}).fill('同步监听验收');
    await dialog.getByRole('button',{name:/选择平台/}).click();
    await dialog.getByRole('checkbox',{name:'同步验收平台'}).check();
    await dialog.getByRole('button',{name:/同步验收平台/}).click();
    await dialog.getByRole('button',{name:'创建并启动',exact:true}).click();await dialog.waitFor({state:'hidden'});
    if(writes.length!==2||writes[1].source_types!=='alarm'||writes[1].time_offset_seconds!==15||writes[0].interval_minutes!==120)throw Error('Wrong source or time window '+JSON.stringify(writes));
    if(errors.length)throw Error(errors.join(';'));
    await page.screenshot({path:'output/playwright/smarteye-ledger-sync.png'});
    return {checks:['Alarm count, matched, unmatched and failures visible','Scheduled interval labels its database window','Watch submits alarm-only source and fixed ±15 seconds'],scope:'Actual LAN UI; task creation and status responses intercepted; no real download or scheduler created'};
  }finally{await page.goto('about:blank');await page.unroute(pattern);}
}
