async page => {
  await page.bringToFront();await page.unrouteAll();await page.emulateMedia({reducedMotion:'no-preference'});
  const writes=[],errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.route(/^http:\/\/localhost:3000\/api\//,route=>{
    if(route.request().method()!=='GET'){writes.push(route.request().url());return route.fulfill({status:405,json:{detail:'Read-only verification'}});}
    return route.continue();
  });
  await page.setViewportSize({width:1440,height:1000});await page.goto('http://localhost:3000/assets');
  await page.locator('.page-toolbar-actions').getByRole('button',{name:/上传图片/}).click();
  const dialog=page.getByRole('dialog',{name:'上传到图片库',exact:true});
  await dialog.getByRole('checkbox',{name:'同时导入 YOLO 标注（ZIP）',exact:true}).check();
  await dialog.getByText('标注保存在图片库，不绑定项目',{exact:true}).waitFor();
  if(await dialog.getByRole('combobox',{name:'接收YOLO标注的项目',exact:true}).count())throw Error('Project binding still required');
  await page.screenshot({path:'output/playwright/yolo-import-live-desktop.png'});
  await page.setViewportSize({width:390,height:844});
  await dialog.getByRole('checkbox',{name:'信任已有标注，直接用于训练/验证（管理员）',exact:true}).scrollIntoViewIfNeeded();
  if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth))throw Error('Mobile import overflow');
  await page.screenshot({path:'output/playwright/yolo-import-live-mobile.png'});
  await dialog.getByRole('button',{name:'关闭',exact:true}).click();
  await page.setViewportSize({width:1440,height:1000});await page.goto('http://localhost:3000/training');
  const run=await page.evaluate(async()=>{
    const response=await fetch('/api/training/runs',{headers:{Authorization:'Bearer '+localStorage.getItem('token')}});
    return (await response.json()).items.find(row=>row.status==='running');
  });
  await page.getByRole('button',{name:run.name,exact:true}).click();
  await page.getByRole('tab',{name:'训练日志',exact:true}).click();
  const log=page.getByRole('log',{name:'训练日志内容'});await log.locator('code').first().waitFor({timeout:30000});
  const content=await log.textContent();if(content.includes('\x1b')||content.includes('\r'))throw Error('Terminal control characters leaked');
  if(!await page.getByRole('button',{name:'删除任务',exact:true}).isDisabled())throw Error('Active real training can be deleted');
  await page.screenshot({path:'output/playwright/training-logs-live-desktop.png'});
  if(writes.length||errors.length)throw Error('Unexpected write/runtime error');
  await page.unrouteAll();
  return{realApiReads:true,projectFreeImportForm:true,realTrainingLogs:true,runningDeleteDisabled:true,writes:0,runtimeErrors:errors};
}
