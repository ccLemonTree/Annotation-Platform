async page=>{
  await page.unrouteAll();page.setDefaultTimeout(12000);await page.setViewportSize({width:1440,height:1000});
  const writes=[],errors=[];page.on('pageerror',error=>errors.push(error.message));
  const pattern=/^http:\/\/192\.168\.1\.90:3000\/api\//;
  await page.route(pattern,route=>{if(route.request().method()!=='GET'){writes.push(route.request().url());return route.abort();}return route.continue();});
  try{
    for(const [name,id] of [['zip','6c7e1713-3d68-4136-bf6d-fa65ee22f68f'],['tar-gz','fe238728-5602-4111-8f3e-1d0afb3704ef'],['folder','ca045444-dba0-4ff2-a71a-d93048f6390a'],['large-101MiB','537fd3a2-4867-4ddf-8f60-6e142f143006']]){
      await page.goto('http://192.168.1.90:3000/model-preview?run_id='+id);
      await page.getByText('未检测到目标',{exact:true}).waitFor();
      await page.waitForFunction(()=>document.querySelector('.model-preview-image img')?.naturalWidth===64);
      if(!await page.locator('.model-preview-image img').evaluate(el=>el.naturalHeight===48))throw Error('Image dimensions wrong');
      if(await page.locator('.model-preview-picker').evaluateAll(inputs=>inputs.some(el=>getComputedStyle(el).display!=='none')))throw Error('Native input visible');
      if(name==='zip')await page.screenshot({path:'output/playwright/preview-archives-live.png'});
    }
    if(writes.length||errors.length)throw Error(JSON.stringify({writes,errors}));
    return {realLANUI:true,verifiedInputs:['ZIP','TAR.GZ','folder relative paths','101MiB ZIP'],actualModel:'YOLOv26 Fire',businessWrites:0,scope:'Read-only display of owned actual inference results'};
  }finally{await page.goto('about:blank');await page.unroute(pattern);}
}
