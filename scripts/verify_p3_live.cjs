async (page) => {
  const checks=[], errors=[];
  let writes=0, activityWrites=0;
  const error=event=>errors.push(event.message);
  const guard=async route=>{
    const req=route.request(),path=new URL(req.url()).pathname;
    if(path==='/api/analytics/activity'){activityWrites++;return route.fulfill({json:{duplicate:false,accepted_seconds:0}});}
    if(!['GET','HEAD','OPTIONS'].includes(req.method())){writes++;return route.abort();}
    return route.continue();
  };
  page.on('pageerror',error);await page.route('**/api/**',guard);
  try{
    await page.setViewportSize({width:1440,height:900});
    for(const [path,ready,file] of [
      ['/automation','自动处理任务','automation-live.png'],
      ['/image-semantics','图片描述与向量检索','image-semantics-live.png'],
      ['/analytics','最新版本质量流程','annotation-analytics-live.png'],
    ]){
      await page.goto('http://localhost:3000'+path);
      if(path==='/automation')await page.getByRole('button',{name:'创建任务',exact:true}).waitFor();
      else if(path==='/image-semantics')await page.getByRole('button',{name:'生成当前图片集',exact:true}).waitFor();
      else await page.getByText(ready,{exact:true}).waitFor();
      await page.waitForFunction(()=>!document.querySelector('.ant-spin-spinning'));
      if(path==='/image-semantics')await page.waitForFunction(()=>[...document.querySelectorAll('tbody img')].every(image=>image.naturalWidth>0));
      await page.screenshot({path:'output/playwright/'+file,fullPage:true});
      await page.setViewportSize({width:390,height:844});
      await page.waitForFunction(()=>document.documentElement.scrollWidth<=innerWidth+1);
      if(await page.locator('html').evaluate(el=>el.scrollWidth>el.clientWidth+1))throw Error('Narrow overflow '+path);
      await page.setViewportSize({width:1440,height:900});
      checks.push('real '+path+' reads and narrow layout');
    }
    await page.goto('http://localhost:3000/annotate/3d28f530-adf8-4556-a493-d82f14e4b410');
    await page.getByRole('img',{name:'检测画框区域'}).waitFor();
    await page.getByText('标注计时',{exact:true}).waitFor();
    const body=await page.evaluate(()=>({height:document.documentElement.scrollHeight,viewport:innerHeight}));
    if(body.height>body.viewport+1)throw Error('Workbench lost viewport fit');
    checks.push('real annotated asset and activity tracker preserve desktop viewport');
    await page.goto('http://localhost:3000/assets');
    await page.locator('tbody .ant-image').first().click();
    await page.getByRole('button',{name:'下一张',exact:true}).waitFor();
    await page.keyboard.press('ArrowRight');
    await page.getByText('2 / 4676',{exact:true}).waitFor();
    await page.keyboard.press('Escape');
    checks.push('real image library arrow navigation');
    if(writes||errors.length)throw Error(JSON.stringify({writes,errors}));
    return {checks,activityWritesIntercepted:activityWrites,businessWrites:writes,errors};
  } finally {
    page.off('pageerror',error);await page.unroute('**/api/**',guard);
    await page.goto('http://localhost:3000/automation');
  }
}
