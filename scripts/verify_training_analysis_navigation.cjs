async page=>{
  await page.unrouteAll();page.setDefaultTimeout(20000);
  const pattern=/^http:\/\/192\.168\.1\.90:3000\/api\//;
  let writes=0;
  await page.route(pattern,async route=>{
    if(route.request().method()!=='GET'){writes++;await route.abort();return;}
    await route.continue();
  });
  try{
    await page.goto('http://192.168.1.90:3000/training');
    await page.getByRole('row').filter({hasText:'detr1016烟火训练7'}).getByRole('button',{name:'效果分析与对比',exact:true}).click();
    await page.getByRole('link',{name:'配置分析大模型 / 默认提示词 →',exact:true}).click();
    try{await page.waitForURL('**/models',{timeout:4000});}
    catch{throw Error('配置入口应进入 /models，实际进入 '+new URL(page.url()).pathname);}
    const row=page.getByRole('row').filter({hasText:'Qwen3.8-27B'});
    await row.getByRole('button',{name:'编辑',exact:true}).click();
    await page.getByRole('textbox',{name:'训练效果分析提示词',exact:true}).waitFor();
    if(writes)throw Error('Navigation check must not save model configuration');
    return {url:page.url(),trainingPromptEditable:true,writes};
  }finally{await page.goto('about:blank');await page.unroute(pattern);}
}
