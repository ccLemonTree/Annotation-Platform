async page => {
  await page.unrouteAll();const writes=[],errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  await page.route(/^http:\/\/localhost:3000\/api\//,route=>{
    const req=route.request(),path=new URL(req.url()).pathname;
    if(req.method()!=='GET'){writes.push(path);return route.fulfill({status:405,json:{detail:'只读验收禁止创建业务任务'}});}
    if(path==='/api/validation/sets')return route.fulfill({json:{items:[{id:'22222222-2222-4222-8222-222222222222',name:'只读表单验收',labels:['fire','smoke'],total:1,source:{kind:'project'}}],total:1}});
    return route.continue();
  });
  await page.setViewportSize({width:1440,height:1000});await page.goto('http://localhost:3000/validation');
  await page.getByRole('button',{name:'验证权重',exact:true}).click();
  const dialog=page.getByRole('dialog',{name:'验证训练权重',exact:true});
  await dialog.getByRole('combobox',{name:'权重来源',exact:true}).click();
  await page.locator('.ant-select-dropdown:visible').getByText('服务器已有权重路径',{exact:true}).click();
  await dialog.getByRole('combobox',{name:'权重模型类型',exact:true}).click();
  await page.locator('.ant-select-dropdown:visible').getByText('RF-DETR Medium',{exact:true}).click();
  const names=dialog.getByRole('combobox',{name:'训练类别顺序',exact:true});
  await names.fill('fire');await names.press('Enter');await names.fill('smoke');await names.press('Enter');await names.press('Escape');
  await dialog.getByRole('textbox',{name:'服务器权重路径',exact:true}).fill('/ai/chen/train/rf-detr/outputs/rfdetr_medium/checkpoint_best_total.pth');
  await page.screenshot({path:'output/playwright/checkpoint-live-desktop.png'});
  await page.setViewportSize({width:390,height:844});
  await dialog.getByRole('combobox',{name:'训练类别顺序',exact:true}).scrollIntoViewIfNeeded();
  if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth))throw Error('Mobile overflow');
  await page.screenshot({path:'output/playwright/checkpoint-live-mobile.png'});
  await dialog.getByRole('button',{name:/取\s*消/,exact:true}).click();
  if(writes.length||errors.length)throw Error('Write/runtime error in read-only verification');
  return{realServerAndTrainingReads:true,validationSetList:'single read-only fixture for opening form; no saved set created',writes:0,runtimeErrors:errors};
}
