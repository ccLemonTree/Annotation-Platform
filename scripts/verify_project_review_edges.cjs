async(page)=>{
  const id='e251de70-5c16-46f4-9cd9-d90a2262539e',checks=[];
  const headers={Authorization:'Bearer '+await page.evaluate(()=>localStorage.getItem('token'))};
  const base=await(await page.request.get('http://localhost:3000/api/annotations?project_id='+id,{headers})).json();
  const routePattern='http://localhost:3000/api/annotations?**';
  let mode='failure',release,started=false;
  const gate=new Promise(resolve=>{release=resolve;});
  const scoped=[];
  await page.route(routePattern,async route=>{
    const query=new URL(route.request().url()).searchParams;
    scoped.push(query.get('project_id'));
    if(mode==='failure'){await route.fulfill({status:500,json:{detail:'review fixture failure'}});return;}
    if(mode==='race'&&query.get('review_status')==='pending'){
      started=true;await gate;await route.fulfill({json:base});return;
    }
    if(mode==='pagination'){await route.fulfill({json:{items:[],total:21,page:Number(query.get('page')||1),page_size:20}});return;}
    await route.fulfill({json:{items:[],total:0,page:1,page_size:20}});
  });
  try{
    await page.goto('http://localhost:3000/annotations?project_id='+id);
    await page.getByText('读取失败，请重试或返回标注项目重新选择',{exact:true}).waitFor();
    if(new URL(page.url()).searchParams.get('project_id')!==id)throw Error('Error lost project');
    checks.push('read failure keeps project and offers inline retry');
    mode='empty';await page.getByRole('button',{name:'重试读取',exact:true}).click();
    const start=page.getByRole('link',{name:'前往项目图片开始标注',exact:true});
    await start.waitFor();
    if(!((await start.getAttribute('href'))||'').includes(id))throw Error('Empty-project entry points elsewhere');
    checks.push('retry recovers and empty project points to its own images');
    mode='pagination';await page.goto('http://localhost:3000/annotations?project_id='+id+'&page=2');
    await page.getByText('共 21 张已标注图片',{exact:true}).waitFor();
    const filter=page.getByRole('combobox',{name:'项目审核状态'});
    await filter.fill('已通过');await filter.press('ArrowDown');await filter.press('Enter');
    await page.waitForURL(url=>url.searchParams.get('page')==='1'&&url.searchParams.get('review_status')==='approved');
    checks.push('status change resets pagination in URL');
    mode='race';await page.goto('http://localhost:3000/annotations?project_id='+id+'&review_status=pending');
    await filter.waitFor();
    if(!started)await page.waitForRequest(request=>request.url().includes('/api/annotations?')&&request.url().includes('review_status=pending'));
    await filter.fill('已通过');await filter.press('ArrowDown');await filter.press('Enter');
    await page.getByText('此项目没有符合审核状态的标注',{exact:true}).waitFor();
    const stale=page.waitForResponse(response=>response.url().includes('/api/annotations?')&&response.url().includes('review_status=pending'));
    release();await stale;
    await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
    if(await page.getByRole('link',{name:'打开标注 / 审核',exact:true}).count())throw Error('Late response replaced new filter');
    checks.push('late previous-filter response cannot replace current results');
    if(!scoped.length||scoped.some(project=>project!==id))throw Error('Unscoped annotation request');
    checks.push('every annotation query stays scoped to selected project');
    await page.setViewportSize({width:390,height:844});
    await page.goto('http://localhost:3000/annotations');
    await page.getByRole('region',{name:'标注项目 test-img',exact:true}).waitFor();
    await page.waitForFunction(()=>parseInt(getComputedStyle(document.querySelector('.platform-main')).marginLeft)===64);
    if(await page.locator('html').evaluate(el=>el.scrollWidth>el.clientWidth+1))throw Error('Project cards overflow');
    checks.push('project overview cards fit narrow viewport');
    await page.screenshot({path:'output/playwright/review-projects-mobile.png'});
    await page.setViewportSize({width:1440,height:1000});
    return {checks,scope:'read responses intercepted for failure/empty/pagination/race; no writes'};
  }finally{release();await page.unroute(routePattern);}
}
