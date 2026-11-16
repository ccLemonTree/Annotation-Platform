async page=>{
  const base='http://127.0.0.1:3017',freshId='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',savedId='bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb';
  const saved={target_label:'car',fields:[{key:'plate',name:'车牌',instruction:'完整清晰字符',options:[]}]};
  const projects={
    [freshId]:{id:freshId,project_name:'未配置项目',task_type:'caption',label_schema:{}},
    [savedId]:{id:savedId,project_name:'已有配置项目',task_type:'caption',label_schema:{object_attributes:saved}},
  };
  const writes=[],errors=[];let template;
  await page.addInitScript(()=>localStorage.setItem('token','isolated-blank-attributes'));
  page.on('pageerror',error=>errors.push(error.message));
  await page.route(/^http:\/\/127\.0\.0\.1:3017\/api(?:\/|\?|$)/,async route=>{
    const request=route.request(),path=new URL(request.url()).pathname;
    if(request.method()==='POST'&&path==='/api/attribute-templates'){
      template={id:'cccccccc-cccc-4ccc-8ccc-cccccccccccc',...request.postDataJSON()};return route.fulfill({json:template});
    }
    if(request.method()==='PUT'&&path.startsWith('/api/projects/')){
      const body=request.postDataJSON();writes.push({path,body});const project=projects[path.split('/').pop()];project.label_schema=body.label_schema;return route.fulfill({json:project});
    }
    if(request.method()!=='GET')throw Error('Unexpected write '+path);
    let data=[];
    if(path==='/api/auth/me')data={id:1,username:'fixture',role:'admin',tenant_id:1};
    else if(path===`/api/projects/${freshId}`)data=projects[freshId];
    else if(path===`/api/projects/${savedId}`)data=projects[savedId];
    else if(path==='/api/attribute-templates')data=template?[template]:[];
    else if(path==='/api/assets'||path.endsWith('/attributes'))data={items:[],total:0};
    else if(['/api/model-services','/api/model-services/prompt-defaults','/api/attribute-templates'].includes(path)||path.endsWith('/work/jobs'))data=[];
    else {errors.push(`Unexpected API GET ${path}`);return route.fulfill({status:500,json:{detail:'Unexpected API'}});}
    return route.fulfill({headers:{'X-Asset-Crop-Depth-Filter':'1','X-Asset-Attribute-Filter':'1','X-Asset-Filename-Filter':'1'},json:data});
  });
  await page.goto(base+`/projects/${freshId}/attributes`);
  await page.getByRole('button',{name:'配置目标与属性',exact:true}).click();
  await page.getByRole('combobox',{name:'属性模板',exact:true}).waitFor();
  if(await page.getByLabel('属性名称 1',{exact:true}).count())throw Error('Unconfigured project still defaults to vehicle attributes');
  await page.getByLabel('属性分析目标标签',{exact:true}).fill('dog');await page.getByLabel('属性分析目标标签',{exact:true}).press('Enter');await page.getByLabel('属性分析目标标签',{exact:true}).press('Escape');
  if(!await page.getByRole('button',{name:'保存为模板',exact:true}).isDisabled())throw Error('Zero-field template can be saved');
  if(!await page.getByRole('button',{name:'保存属性配置',exact:true}).isDisabled())throw Error('Zero-field project config can be saved');
  await page.getByRole('button',{name:'添加属性',exact:true}).click();await page.getByLabel('属性名称 1',{exact:true}).fill('毛色');
  await page.getByRole('button',{name:'保存为模板',exact:true}).click();
  const modal=page.getByRole('group',{name:'保存属性模板',exact:true});await modal.getByLabel('模板名称',{exact:true}).fill('犬类属性');await modal.getByRole('button',{name:'保存模板',exact:true}).click();await modal.waitFor({state:'hidden'});
  if(template?.config.target_label!=='dog'||template.config.fields.length!==1||template.config.fields[0].name!=='毛色'||writes.length)throw Error('Custom blank template includes vehicle fields or changes project before saving');
  await page.goto(base+`/projects/${savedId}/attributes`);
  await page.getByRole('button',{name:'查看 / 编辑配置',exact:true}).click();
  await page.getByLabel('属性名称 1',{exact:true}).waitFor();
  if(await page.getByLabel('属性名称 1',{exact:true}).inputValue()!=='车牌')throw Error('Existing saved schema lost');
  await page.getByRole('button',{name:'新建空白配置',exact:true}).click();
  if(await page.getByLabel('属性名称 1',{exact:true}).count()||writes.length||projects[savedId].label_schema.object_attributes!==saved)throw Error('Reset modified saved project or failed to clear draft');
  await page.getByRole('combobox',{name:'属性模板',exact:true}).click();await page.getByText('犬类属性',{exact:true}).last().click();await page.getByRole('button',{name:'载入模板',exact:true}).click();
  if(await page.getByLabel('属性名称 1',{exact:true}).inputValue()!=='毛色')throw Error('Custom template not reusable');
  await page.getByRole('button',{name:/^移\s*除$/}).click();
  if(await page.getByLabel('属性名称 1',{exact:true}).count()||!await page.getByRole('button',{name:'保存属性配置',exact:true}).isDisabled())throw Error('Last field removal not handled safely');
  await page.getByRole('button',{name:'添加属性',exact:true}).click();await page.getByLabel('属性名称 1',{exact:true}).fill('毛色');
  await page.getByRole('button',{name:'保存属性配置',exact:true}).click();await page.getByText('检测标签：dog',{exact:true}).waitFor();
  const schema=writes.at(-1)?.body.label_schema.object_attributes;
  if(schema?.target_label!=='dog'||schema.fields.length!==1||schema.fields[0].name!=='毛色')throw Error('Explicitly saving custom config failed');
  await page.screenshot({path:'output/playwright/attribute-blank-custom-dog.png',fullPage:true});
  if(errors.length)throw Error(errors.join('\n'));
  return {passed:true,no_implicit_vehicle_schema:true,existing_saved_schema_preserved:true,blank_reset_is_draft_only:true,zero_fields_cannot_save:true,custom_dog_template:true,custom_template_reusable:true,remove_last_field:true,explicit_project_save:true,errors};
}
