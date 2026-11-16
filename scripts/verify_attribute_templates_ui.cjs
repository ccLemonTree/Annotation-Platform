async page=>{
  const base='http://127.0.0.1:3017',id='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
  const project={id,project_name:'属性模板回归',task_type:'caption',label_schema:{object_attributes:{target_label:'dog',fields:[{key:'color',name:'颜色',instruction:'',options:[]}]}}};
  let templates=[];const writes=[],errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  await page.addInitScript(()=>localStorage.setItem('token','isolated-templates'));
  await page.route(/^http:\/\/127\.0\.0\.1:3017\/api(?:\/|\?|$)/,async route=>{
    const request=route.request(),path=new URL(request.url()).pathname;
    if(request.method()==='POST'&&path==='/api/attribute-templates'){
      const body=request.postDataJSON();const row={id:'11111111-1111-4111-8111-111111111111',...body};templates.push(row);return route.fulfill({json:row});
    }
    if(request.method()==='DELETE'&&path.startsWith('/api/attribute-templates/')){templates=[];return route.fulfill({status:204});}
    if(request.method()==='PUT'&&path===`/api/projects/${id}`){writes.push(request.postDataJSON());project.label_schema=writes.at(-1).label_schema;return route.fulfill({json:project});}
    if(request.method()!=='GET')throw Error('Unexpected write '+path);
    let data=[];
    if(path==='/api/auth/me')data={id:1,username:'fixture',role:'admin',tenant_id:1};
    else if(path===`/api/projects/${id}`)data=project;
    else if(path==='/api/attribute-templates')data=templates;
    else if(path==='/api/assets'||path.endsWith('/attributes'))data={items:[],total:0};
    else if(['/api/model-services','/api/model-services/prompt-defaults','/api/attribute-templates'].includes(path)||path.endsWith('/work/jobs'))data=[];
    else {errors.push(`Unexpected API GET ${path}`);return route.fulfill({status:500,json:{detail:'Unexpected API'}});}
    return route.fulfill({headers:{'X-Asset-Crop-Depth-Filter':'1','X-Asset-Attribute-Filter':'1','X-Asset-Filename-Filter':'1'},json:data});
  });
  const load=async name=>{
    await page.getByRole('combobox',{name:'属性模板',exact:true}).click();
    await page.locator('.ant-select-dropdown:visible').getByText(name,{exact:true}).click();
    await page.getByRole('button',{name:'载入模板',exact:true}).click();
  };
  await page.goto(base+`/projects/${id}/attributes`);
  await page.getByText('检测标签：dog',{exact:true}).waitFor();
  await page.getByRole('button',{name:'查看 / 编辑配置',exact:true}).click();
  await page.waitForFunction(()=>document.activeElement?.getAttribute('aria-label')==='属性分析目标标签');
  await load('车辆');
  await page.getByText('检测标签（待保存）：car',{exact:true}).waitFor({timeout:2500});
  if(project.label_schema.object_attributes.target_label!=='dog'||writes.length)throw Error('Draft template altered saved target before save');
  await page.getByRole('button',{name:'新建空白配置',exact:true}).click();
  await page.getByText('检测标签（待保存）：未设置',{exact:true}).waitFor();
  await load('人员');
  if(await page.getByLabel('属性名称 1',{exact:true}).inputValue()!=='上身服饰')throw Error('Person template not loaded');
  await page.getByLabel('属性分析目标标签').fill('bicycle');await page.getByLabel('属性分析目标标签').press('Enter');await page.getByLabel('属性分析目标标签').press('Escape');
  await page.getByText('检测标签（待保存）：person、bicycle',{exact:true}).waitFor();
  await page.getByLabel('属性名称 1',{exact:true}).fill('人员上衣');
  await page.getByRole('button',{name:'保存为模板',exact:true}).click();
  const dialog=page.getByRole('group',{name:'保存属性模板',exact:true});
  await dialog.getByLabel('模板名称',{exact:true}).fill('人员和骑行目标');
  await dialog.getByRole('button',{name:'保存模板',exact:true}).click();await dialog.waitFor({state:'hidden'});
  if(writes.length||templates[0].config.target_label.join()!=='person,bicycle'||templates[0].config.fields[0].name!=='人员上衣')throw Error('Saving template modified project or lost labels');
  await load('车辆');
  if(await page.getByLabel('属性名称 1',{exact:true}).inputValue()!=='车牌')throw Error('Vehicle template missing');
  await load('通用物品');
  if(await page.getByLabel('属性名称 1',{exact:true}).inputValue()!=='物品类型')throw Error('Object template missing');
  await page.reload();
  await page.getByRole('button',{name:'查看 / 编辑配置',exact:true}).click();
  await page.waitForFunction(()=>document.activeElement?.getAttribute('aria-label')==='属性分析目标标签');
  await load('人员和骑行目标');
  await page.getByRole('button',{name:'保存属性配置',exact:true}).click();
  await page.getByText('检测标签：person、bicycle',{exact:true}).waitFor();
  if(writes.at(-1).label_schema.object_attributes.target_label.join()!=='person,bicycle')throw Error('Multiple target labels not persisted');
  await page.getByRole('button',{name:'修改目标标签',exact:true}).click();
  await page.waitForFunction(()=>document.activeElement?.getAttribute('aria-label')==='属性分析目标标签');
  await page.screenshot({path:'output/playwright/attribute-templates-multi-label.png',fullPage:true});
  await page.getByRole('combobox',{name:'属性模板',exact:true}).click();
  await page.locator('.ant-select-dropdown:visible').getByText('人员和骑行目标',{exact:true}).click();
  await page.getByRole('button',{name:'删除模板',exact:true}).click();
  await page.getByRole('button',{name:/^确\s*定$/}).click();
  if(templates.length)throw Error('Custom template not deleted');
  if(project.label_schema.object_attributes.target_label.join()!=='person,bicycle')throw Error('Deleting template changed project config');
  if(errors.length)throw Error(errors.join('\n'));
  return {passed:true,builtins:['人员','车辆','通用物品'],draft_targets_synchronized:true,empty_draft_target_visible:true,saved_target_unchanged_before_save:true,custom_save_reload_apply_delete:true,multiple_target_labels:true,edit_target_shortcut:true,deletion_preserves_project:true,errors};
}
