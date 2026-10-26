async (page) => {
  const original = '01ba049a-bc58-4749-848e-c3ce35979269', projectId = 'e251de70-5c16-46f4-9cd9-d90a2262539e';
  const pattern = 'http://localhost:3000/api/annotations**';
  let failure = 0, writes = 0, current;
  const checks = [];
  await page.route(pattern, async route => {
    const request = route.request(), url = new URL(request.url());
    if (url.pathname.endsWith('/events')) { await route.fulfill({json:[]});return; }
    if (request.method()==='GET') { await route.fulfill({json:{items:current && url.searchParams.get('asset_id')===current.asset_id ? [current]:[],total:current && url.searchParams.get('asset_id')===current.asset_id ? 1:0,page:1,page_size:100}});return; }
    writes++;
    if(failure) { await route.fulfill({status:failure,json:{detail:'autosave fixture failure'}});return; }
    current={id:'11111111-1111-4111-8111-111111111111',asset_id:original,annotation_data:request.postDataJSON().annotation_data,version:1,is_latest:true,review_status:'pending'};
    await route.fulfill({json:current});
  });
  const projectRoute='http://localhost:3000/api/projects/'+projectId;
  await page.route(projectRoute,async route=>{const response=await route.fetch();await route.fulfill({json:{...await response.json(),label_schema:{labels:['person']}}});});
  try {
    await page.goto('http://localhost:3000/annotate/'+original);
    const canvas=page.getByRole('img',{name:'检测画框区域'});
    await canvas.waitFor();
    const rectangle=async()=> {
      const points=await canvas.evaluate(svg=>{
        const start=svg.createSVGPoint();start.x=200;start.y=200;
        const end=svg.createSVGPoint();end.x=350;end.y=350;
        const matrix=svg.getScreenCTM();const a=start.matrixTransform(matrix),b=end.matrixTransform(matrix);return {a:{x:a.x,y:a.y},b:{x:b.x,y:b.y}};
      });
      await page.mouse.move(points.a.x,points.a.y);await page.mouse.down();
      await page.mouse.move(points.b.x,points.b.y,{steps:3});await page.mouse.up();
    };
    await rectangle();
    await page.getByRole('button',{name:'上一张',exact:true}).click();
    await page.waitForURL(url=>url.pathname.startsWith('/annotate/')&&!url.pathname.endsWith(original));
    if(writes!==1) throw Error('Auto-save duplicated or missing');
    checks.push('previous button saves once before navigation');
    current=undefined;
    await page.goto('http://localhost:3000/annotate/'+original);await canvas.waitFor();
    for(const status of [500,409]) {
      failure=status; await rectangle();
      const completed=page.waitForResponse(response=>response.url().startsWith('http://localhost:3000/api/annotations')&&['POST','PUT'].includes(response.request().method()));
      await canvas.focus();await page.keyboard.press('a');
      if((await completed).status()!==status)throw Error('Unexpected save response');
      await page.getByText('保存失败；若版本冲突，请先记录当前修改，再重新加载最新版本',{exact:true}).waitFor();
      if(!page.url().endsWith(original)||await canvas.locator('g > rect:not([data-resize])').count()!==1) throw Error('Failed save lost image/draft');
      await page.waitForFunction(()=>[...document.querySelectorAll('button')].some(button=>button.textContent.replace(/\s/g,'')==='上一张'&&!button.disabled));
      checks.push(status+' stays on original image with draft');
      await canvas.focus();await page.keyboard.press('Control+z');
    }
    failure=0;
    await canvas.focus();await page.keyboard.press('a');
    await page.waitForURL(url=>url.pathname.startsWith('/annotate/')&&!url.pathname.endsWith(original));
    if(writes!==3)throw Error('Unchanged navigation created a version');
    checks.push('unchanged navigation does not write annotation');
    current={id:'11111111-1111-4111-8111-111111111111',asset_id:original,
      annotation_data:{boxes:[{label:'person',x:200,y:200,width:150,height:150}]},version:1,is_latest:true,review_status:'pending'};
    await page.goto('http://localhost:3000/annotate/'+original);await canvas.waitFor();
    await page.getByRole('button',{name:'1 · person',exact:true}).click();
    await canvas.focus();await page.keyboard.press('Delete');await page.keyboard.press('a');
    await page.getByText('无框时请先确认此图没有项目中的目标，自动保存未完成',{exact:true}).waitFor();
    if(!page.url().endsWith(original)||writes!==3)throw Error('Empty draft saved without negative confirmation');
    checks.push('deleting last box requires negative confirmation before navigation');
    await page.getByRole('checkbox',{name:'此图没有项目中的目标（负样本）'}).check();
    await canvas.focus();await page.keyboard.press('a');
    await page.waitForURL(url=>url.pathname.startsWith('/annotate/')&&!url.pathname.endsWith(original));
    if(writes!==4||current.annotation_data.boxes.length!==0)throw Error('Confirmed negative not saved');
    checks.push('confirmed negative auto-saves before navigation');
    return {checks,scope:'browser intercepts all annotation writes; real image read only'};
  } finally {await page.unroute(pattern);await page.unroute(projectRoute);}
}
