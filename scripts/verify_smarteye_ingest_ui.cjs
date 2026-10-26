async(page)=>{
  const routes=['http://localhost:3000/api/smarteye/platform-list','http://localhost:3000/api/smarteye/platforms','http://localhost:3000/api/smarteye/download'];
  let created;
  await page.route(routes[0],route=>route.fulfill({json:{code:0,data:[{label:'场景测试平台',value:'fixture',alarmTypes:{人:'1'}}]}}));
  await page.route(routes[1],route=>route.fulfill({json:{code:0,data:{records:[{id:'fixture',platform:'场景测试平台',picUrl:'',device:'fixture'}],total:1,pages:1}}}));
  await page.route(routes[2],async route=>{
    created=await route.request().allHeaders();
    await route.fulfill({json:{code:1,message:'界面验收，不启动真实拉取'}});
  });
  try{
    await page.goto('http://localhost:3000/smarteye');
    const frame=page.frameLocator('iframe[title="SmartEye 数据拉取"]');
    const target=frame.getByRole('combobox',{name:'拉取目标图片集',exact:true});
    await target.fill('历史图片');await target.press('ArrowDown');await target.press('Enter');
    const minimum=frame.getByRole('spinbutton',{name:'拉取长边阈值',exact:true});
    await minimum.waitFor();await minimum.fill('1080');await minimum.press('Tab');
    await frame.getByRole('button',{name:'图片集设置',exact:true}).click();
    const config=frame.getByRole('dialog',{name:'图片集入库与场景设置',exact:true});
    await config.getByRole('textbox',{name:'模型服务地址',exact:true}).waitFor();
    await config.getByRole('button',{name:'关闭',exact:true}).click();
    await frame.getByRole('button',{name:'选择查询平台',exact:true}).click();
    await frame.getByRole('checkbox',{name:'场景测试平台',exact:true}).check();
    await frame.getByRole('button',{name:'选择查询平台',exact:true}).click();
    await frame.getByRole('button',{name:/查询$/}).click();
    await frame.getByRole('button',{name:/下载所有图片/}).click();
    await frame.getByText(/界面验收，不启动真实拉取/).waitFor();
    if(!created['x-smarteye-collection']||created['x-smarteye-min-long-edge']!=='1080'||created['x-smarteye-purpose']!=='download'||!created.authorization?.startsWith('Bearer '))throw Error('Task did not carry selected ingestion policy');
    await page.setViewportSize({width:390,height:844});
    if(await frame.locator('html').evaluate(el=>el.scrollWidth>el.clientWidth+1))throw Error('SmartEye ingestion controls overflow');
    await page.screenshot({path:'output/playwright/smarteye-ingest-mobile.png'});
    return {checks:['SmartEye shares platform model configuration dialog','download creation carries chosen long-edge threshold and authentication','SmartEye ingestion controls fit narrow viewport'],scope:'query/download requests intercepted; no real subordinate platform contacted'};
  }finally{for(const route of routes)await page.unroute(route);await page.setViewportSize({width:1440,height:1000});}
}
