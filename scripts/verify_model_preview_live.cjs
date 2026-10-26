async page=>{
  await page.unrouteAll();page.setDefaultTimeout(15000);await page.setViewportSize({width:1440,height:1000});
  const writes=[],errors=[];page.on('pageerror',error=>errors.push(error.message));
  const pattern=/^http:\/\/192\.168\.1\.90:3000\/api\//;
  await page.route(pattern,route=>{
    if(route.request().method()!=='GET'){writes.push(route.request().url());return route.abort();}
    return route.continue();
  });
  try{
    for(const [label,id] of [['service','057435bf-4fef-4126-bdd2-1d5aedd1124d'],['checkpoint','8989e05a-cfcc-4dab-86c4-7d49e2513701']]){
      await page.goto('http://192.168.1.90:3000/model-preview?run_id='+id);
      await page.getByText('未检测到目标',{exact:true}).waitFor();
      const image=page.getByRole('img',{name:'owned-preview.png',exact:true});await image.waitFor();
      if(!await image.evaluate(el=>el.complete&&el.naturalWidth===640&&el.naturalHeight===480))throw Error('Real preview image did not load');
      if(await page.locator('.model-preview-picker').isVisible())throw Error('Hidden native file picker became visible');
      await page.screenshot({path:'output/playwright/model-preview-live-'+label+'.png'});
    }
    if(writes.length||errors.length)throw Error(JSON.stringify({writes,errors}));
    return {actualLANUI:true,realDetectionService:'completed; uploaded fixture visible',realTrainingCheckpoint:'completed on CPU; uploaded fixture visible',businessWrites:0,scope:'Read-only browser checks of owned results; synthetic picture used, no accuracy claim'};
  }finally{await page.goto('about:blank');await page.unroute(pattern);}
}
