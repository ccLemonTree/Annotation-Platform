async(page)=>{
  const project='e251de70-5c16-46f4-9cd9-d90a2262539e',asset='01ba049a-bc58-4749-848e-c3ce35979269';
  const headers={Authorization:'Bearer '+await page.evaluate(()=>localStorage.getItem('token'))};
  const records=await(await page.request.get('http://localhost:3000/api/annotations?asset_id='+asset,{headers})).json();
  let record={...records.items.find(item=>item.is_latest),review_status:'pending'},writes=[];
  const pattern='http://localhost:3000/api/annotations**';
  await page.route(pattern,async route=>{
    const request=route.request(),url=new URL(request.url());
    if(request.method()==='GET'){
      if(url.pathname==='/api/annotations'&&url.searchParams.get('asset_id')===asset){
        await route.fulfill({json:{items:[record],total:1,page:1,page_size:100}});return;
      }
      await route.continue();return;
    }
    if(request.method()==='POST'&&url.pathname==='/api/annotations/'+record.id+'/review'){
      writes.push(request.postDataJSON());
      record={...record,review_status:writes.at(-1).approved?'approved':'rejected'};
      await route.fulfill({json:record});return;
    }
    await route.abort();
  });
  try{
    await page.goto('http://localhost:3000/annotate/'+asset+'?from=review&project_id='+project);
    await page.getByRole('img',{name:'检测画框区域'}).waitFor();
    await page.getByRole('button',{name:'审核通过',exact:true}).click();
    await page.locator('.ant-tag').filter({hasText:'v'+record.version+' · 已通过'}).waitFor();
    await page.getByRole('button',{name:'驳 回',exact:true}).click();
    await page.locator('.ant-tag').filter({hasText:'v'+record.version+' · 已驳回'}).waitFor();
    if(writes.length!==2||!writes[0].approved||writes[1].approved)throw Error('Review actions lost');
    await page.getByRole('link',{name:'返回 test-img 标注与审核',exact:true}).click();
    await page.waitForURL(url=>url.pathname==='/annotations'&&url.searchParams.get('project_id')===project);
    return {checks:['visual workbench approval still submits existing review contract','visual workbench rejection still submits existing review contract','review actions return to owning project'],scope:'all annotation writes intercepted; approved/rejected only in fixture'};
  }finally{await page.unroute(pattern);}
}
