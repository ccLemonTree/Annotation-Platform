async (page) => {
  const id='e251de70-5c16-46f4-9cd9-d90a2262539e';
  const project={id,project_name:'test-img',task_type:'detection',status:'active',description:'',label_schema:{labels:['person']},created_at:'2026-10-13T00:00:00Z'};
  const list='http://localhost:3000/api/projects?**',detail='http://localhost:3000/api/projects/'+id,annotations='http://localhost:3000/api/annotations?**';
  let payload;
  await page.route(list,route=>route.fulfill({json:{items:[project],total:1,page:1,page_size:10}}));
  await page.route(detail,async route=>{payload=route.request().postDataJSON();await route.fulfill({json:{...project,label_schema:payload.label_schema}});});
  await page.route(annotations,route=>route.fulfill({json:{items:[{annotation_data:{boxes:[{label:'人'}]}}],total:1,page:1,page_size:100}}));
  try {
    await page.goto('http://localhost:3000/projects');
    await page.getByRole('button',{name:/编辑/}).click();
    const modal=page.getByRole('dialog',{name:'编辑项目',exact:true});
    await modal.getByRole('combobox',{name:'类别映射 人',exact:true}).waitFor();
    await modal.getByRole('combobox',{name:'类别映射 人',exact:true}).click();
    await modal.getByRole('combobox',{name:'类别映射 人',exact:true}).press('ArrowDown');
    await modal.getByRole('combobox',{name:'类别映射 人',exact:true}).press('Enter');
    await modal.getByRole('button',{name:'确 定',exact:true}).click();
    await modal.waitFor({state:'hidden'});
    if(payload.label_renames['人']!=='person'||payload.base_label_schema.labels[0]!=='person')throw Error('Rename mapping/base config omitted');
    return {checks:['legacy used label appears in mapping','explicit source-to-target mapping submitted','base schema submitted'],scope:'browser-only project update'};
  } finally {await page.unroute(list);await page.unroute(detail);await page.unroute(annotations);}
}
