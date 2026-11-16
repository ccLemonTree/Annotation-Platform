async page=>{
  page.setDefaultTimeout(8000);
  const base=process.env.UPLOAD_TEST_ORIGIN||'http://127.0.0.1:3000',id='11111111-1111-4111-8111-111111111111';
  const project={id,tenant_id:1,project_name:'范围测试',task_type:'detection',status:'active',label_schema:{annotation_scope:'crops',labels:[]},total_assets:23874,annotated_count:0,reviewed_count:0,created_at:'2026-11-09T00:00:00Z'};
  const writes=[];
  await page.addInitScript(()=>localStorage.setItem('token','isolated-scope-test'));
  await page.route(/^https?:\/\/[^/]+\/api\//,route=>{
    const req=route.request(),path=new URL(req.url()).pathname;
    if(path==='/api/auth/me')return route.fulfill({json:{id:1,tenant_id:1,username:'fixture',role:'admin',is_active:true}});
    if(path==='/api/projects/'+id){
      if(req.method()==='PUT'){
        const body=req.postDataJSON();writes.push(body);
        if(body.base_label_schema.annotation_scope!==project.label_schema.annotation_scope)throw Error('Scope change did not fence stale edits');
        project.label_schema=body.label_schema;
      }
      return route.fulfill({json:project});
    }
    if(path.endsWith('/stats'))return route.fulfill({json:{total_assets:project.label_schema.annotation_scope==='crops'?0:project.total_assets,annotated_count:0,reviewed_count:0}});
    if(['/api/assets','/api/annotations'].includes(path))return route.fulfill({json:{items:[],total:0}});
    if(path.endsWith('/work/jobs')||path==='/api/model-services')return route.fulfill({json:[]});
    if(path==='/api/model-services/prompt-defaults')return route.fulfill({json:{}});
    throw Error('Unexpected fixture API '+path);
  });
  const review='/annotations?project_id='+id+'&prepare=1';
  await page.goto(base+review);
  await page.getByText('项目已有 23,874 张图片，当前仅标注裁剪图',{exact:true}).waitFor();
  await page.getByText('还没有可标注的裁剪图',{exact:true}).waitFor();
  if(await page.getByRole('link',{name:'添加图片',exact:true}).count())throw Error('Existing source images still prompt adding images');
  await page.getByText('先设置标注类别，再开始标注',{exact:true}).waitFor();
  if(await page.getByRole('checkbox',{name:'切图后仅标注裁剪图'}).count())throw Error('Crop still offers scope override');
  await page.getByRole('combobox',{name:'项目标注范围'}).click();
  await page.getByText('原图与裁剪图',{exact:true}).last().click();
  await page.getByText('还没有可标注的裁剪图',{exact:true}).waitFor({state:'hidden'});
  if(writes.length!==1||writes[0].label_schema.annotation_scope!=='all')throw Error('Scope was not saved once from preparation');
  await page.goto(base+'/projects/'+id+'/settings');
  await page.getByRole('button',{name:'编辑名称与类别'}).waitFor();
  await page.getByRole('button',{name:'分配项目成员'}).waitFor();
  if(await page.getByRole('combobox',{name:'项目标注范围'}).count()||await page.getByText('高级设置',{exact:true}).count())throw Error('Settings still has the duplicate scope editor');
  project.total_assets=0;
  await page.goto(base+review);
  await page.getByText('先添加图片，再设置标注类别',{exact:true}).waitFor();
  await page.getByRole('link',{name:'添加图片',exact:true}).waitFor();
  return {scopeEditableInPreparation:true,noDuplicateInSettings:true,projectSettingsRetained:true,scopeEmptyExplained:true,addImagesOnlyIfProjectEmpty:true};
}
