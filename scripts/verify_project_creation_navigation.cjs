async(page)=>{
  const existing='e251de70-5c16-46f4-9cd9-d90a2262539e',id='33333333-3333-4333-8333-333333333333';
  const headers={Authorization:'Bearer '+await page.evaluate(()=>localStorage.getItem('token'))};
  const base=await(await page.request.get('http://localhost:3000/api/projects/'+existing,{headers})).json();
  let created;
  const projectPath='http://localhost:3000/api/projects/'+id,postPath='http://localhost:3000/api/projects',assets='http://localhost:3000/api/assets?**';
  await page.route(postPath,async route=>{
    created={...base,...route.request().postDataJSON(),id,project_name:'navigation-new-project',total_assets:0,annotated_count:0,reviewed_count:0};
    await route.fulfill({status:201,json:created});
  });
  await page.route(projectPath,route=>route.fulfill({json:created}));
  await page.route(assets,async route=>{
    if(new URL(route.request().url()).searchParams.get('project_id')===id)await route.fulfill({json:{items:[],total:0,page:1,page_size:20}});
    else await route.continue();
  });
  try{
    await page.goto('http://localhost:3000/projects');
    await page.getByRole('button',{name:/新建项目/}).click();
    const modal=page.getByRole('dialog',{name:'新建项目',exact:true});
    await modal.getByRole('textbox',{name:'* 项目名称',exact:true}).fill('navigation-new-project');
    await modal.getByRole('combobox',{name:'项目标注类别',exact:true}).fill('person');
    await modal.getByRole('combobox',{name:'项目标注类别',exact:true}).press('Enter');
    await modal.getByRole('button',{name:'确 定',exact:true}).click();
    await page.waitForURL('http://localhost:3000/projects/'+id+'/images');
    await page.getByText('navigation-new-project',{exact:true}).waitFor();
    await page.getByRole('button',{name:'从图片库添加',exact:true}).click();
    const picker=page.getByRole('dialog',{name:'从图片库添加到 navigation-new-project',exact:true});
    await picker.waitFor();
    await picker.getByRole('button',{name:'取消选图',exact:true}).click();
    return {checks:['new project goes directly to its image page','empty project has one clear image-selection action'],scope:'project creation intercepted; no real project created'};
  }finally{await page.unroute(postPath);await page.unroute(projectPath);await page.unroute(assets);}
}
