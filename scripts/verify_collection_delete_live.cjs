async page=>{
  await page.unrouteAll();page.setDefaultTimeout(10000);
  const writes=[],errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.route(/^http:\/\/localhost:3000\/api\//,route=>{
    if(route.request().method()!=='GET'){writes.push(route.request().url());return route.abort();}
    return route.continue();
  });
  await page.setViewportSize({width:1440,height:1000});await page.goto('http://localhost:3000/assets');
  const button=page.getByRole('button',{name:'删除图片集',exact:true});await button.waitFor();
  if(!await button.isDisabled())throw Error('无选择时没有禁用');
  await page.getByRole('combobox',{name:'筛选图片集',exact:true}).click();
  await page.locator('.ant-select-dropdown:visible .ant-select-item-option').first().click();
  await button.click();const dialog=page.getByRole('dialog',{name:'删除图片集及独占图片？',exact:true});
  await dialog.getByText(/共 \d+ 张图片：预计删除|未完成的上传|场景分类/).first().waitFor();
  await dialog.screenshot({path:'output/playwright/collection-delete-live-preview.png'});
  await dialog.getByRole('button',{name:/取\s*消/}).click();
  if(writes.length||errors.length)throw Error(JSON.stringify({writes,errors}));
  return {realUI:'Delete action and live preview or active-work guard visible; cancellation verified',businessWriteRequests:writes.length,pageErrors:errors};
}
