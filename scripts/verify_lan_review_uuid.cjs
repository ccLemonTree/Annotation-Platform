async page=>{
  await page.unrouteAll();page.setDefaultTimeout(15000);
  const beats=[],writes=[],errors=[],checks=[];
  const origin='http://192.168.1.90:3000',project='76e29127-e6a3-442a-be12-3324299816c3';
  const pattern=/^http:\/\/192\.168\.1\.90:3000\/api\//;
  const recordError=error=>errors.push(error.message);
  page.on('pageerror',recordError);
  await page.route(pattern,route=>{
    const request=route.request(),path=new URL(request.url()).pathname;
    if(request.method()==='GET')return route.continue();
    writes.push(path);
    if(path==='/api/analytics/activity'){
      beats.push(request.postDataJSON());return route.fulfill({json:{duplicate:false,accepted_seconds:15}});
    }
    return route.fulfill({status:403,json:{detail:'Test forbids business writes'}});
  });
  try{
    await page.setViewportSize({width:1440,height:1000});await page.goto(origin+'/projects/'+project+'/prepare');
    const environment=await page.evaluate(()=>({secure:isSecureContext,uuid:typeof crypto.randomUUID,random:typeof crypto.getRandomValues}));
    if(environment.secure||environment.uuid!=='undefined'||environment.random!=='function')throw Error('Not an actual insecure LAN context');
    await page.getByRole('link',{name:'标注 / 审核',exact:true}).first().click();
    await page.waitForURL(/\/annotate\//);
    await Promise.race([
      page.getByText('等待工作台操作后计时；空闲60秒暂停',{exact:true}).waitFor(),
      page.getByText('crypto.randomUUID is not a function',{exact:true}).waitFor().then(()=>{throw Error('LAN annotation entry crashes: crypto.randomUUID is not a function');})
    ]);
    checks.push('Real project prepare link opens annotation workspace on LAN HTTP');
    const asset=new URL(page.url()).pathname.split('/').pop();
    await page.clock.install();await page.bringToFront();
    const collect=async activity=>{
      const received=page.waitForResponse(response=>response.url().endsWith('/api/analytics/activity'));
      await page.mouse.move(300,190);await page.mouse.move(310,195);
      await page.clock.runFor(16000);await received;
      const beat=beats.at(-1);
      if(!beat||beat.project_id!==project||beat.asset_id!==asset||beat.activity!==activity||!Number.isInteger(beat.sequence)||beat.sequence<1||!/^[\da-f]{8}-[\da-f]{4}-4[\da-f]{3}-[89ab][\da-f]{3}-[\da-f]{12}$/.test(beat.session_id))throw Error('Invalid heartbeat '+JSON.stringify(beat));
      return beat.session_id;
    };
    const annotation=await collect('annotation');
    await page.getByText('审核计时',{exact:true}).click();
    const review=await collect('review');
    if(annotation===review)throw Error('New tracking session reused UUID');
    checks.push('Annotation and review timing emit distinct RFC4122 v4 UUIDs without randomUUID');
    await page.screenshot({path:'output/playwright/lan-review-uuid.png'});
    await page.goto('about:blank');
    if(writes.some(path=>path!=='/api/analytics/activity')||errors.length)throw Error(JSON.stringify({writes,errors}));
    checks.push('No annotation/review writes, no uncaught browser errors; heartbeat writes intercepted');
    return {checks,environment,heartbeatCount:beats.length,businessWrites:0};
  }finally{await page.goto('about:blank');page.off('pageerror',recordError);await page.unroute(pattern);}
}
