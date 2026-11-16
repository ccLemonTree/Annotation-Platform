async page=>{
  await page.unrouteAll({behavior:'ignoreErrors'});page.setDefaultTimeout(7000);
  const base='http://127.0.0.1:3000',id='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',freshId='cccccccc-cccc-4ccc-8ccc-cccccccccccc';
  const fields=Array.from({length:30},(_,i)=>({key:'field'+i,name:'属性'+(i+1),instruction:'判断说明',options:[]}));
  const project={id,project_name:'属性弹窗测试',task_type:'caption',status:'active',label_schema:{object_attributes:{target_label:'car',fields}}};
  const errors=[],unknown=[],writes=[],templates=[];let failSave=true;
  page.on('pageerror',error=>errors.push(error.message));
  await page.addInitScript(()=>localStorage.setItem('token','isolated-attribute-modal'));
  await page.route(/^https?:\/\/[^/]+\/api(?:\/|\?|$)/,async route=>{
    const req=route.request(),url=new URL(req.url()),path=url.pathname,method=req.method();
    if(url.origin!==base){unknown.push(req.url());return route.abort();}
    if(method==='PUT'&&path===`/api/projects/${id}`){
      writes.push(req.postDataJSON());if(failSave)return route.fulfill({status:503,json:{detail:'配置保存失败，请重试'}});
      project.label_schema=writes.at(-1).label_schema;return route.fulfill({json:project});
    }
    if(method==='POST'&&path==='/api/attribute-templates'){
      const template={id:'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',...req.postDataJSON()};templates.push(template);return route.fulfill({json:template});
    }
    if(method!=='GET'){unknown.push(`${method} ${path}`);return route.fulfill({status:500,json:{detail:'Unexpected API'}});}
    if(path==='/api/auth/me')return route.fulfill({json:{id:1,username:'fixture',role:'admin',tenant_id:1}});
    if(path===`/api/projects/${id}`)return route.fulfill({json:project});
    if(path===`/api/projects/${freshId}`)return route.fulfill({json:{id:freshId,project_name:'未配置项目',task_type:'caption',status:'active',label_schema:{}}});
    if(path==='/api/attribute-templates')return route.fulfill({json:templates});
    if(path==='/api/model-services')return route.fulfill({json:[]});
    if(path==='/api/model-services/prompt-defaults')return route.fulfill({json:{}});
    if(path==='/api/assets'||path===`/api/projects/${id}/attributes`)return route.fulfill({headers:{'X-Asset-Crop-Depth-Filter':'1'},json:{items:[],total:0}});
    if(path===`/api/projects/${id}/work/jobs`)return route.fulfill({json:[]});
    unknown.push(`${method} ${path}`);return route.fulfill({status:500,json:{detail:'Unexpected API'}});
  });
  const check=(ok,message)=>{if(!ok)throw Error(message);};
  const modal=()=>page.getByRole('dialog',{name:'目标与属性配置',exact:true});
  const open=()=>page.getByRole('button',{name:'查看 / 编辑配置',exact:true});
  await page.setViewportSize({width:1440,height:900});await page.goto(base+`/projects/${id}/attributes`);
  await open().waitFor();check(await page.getByLabel('属性名称 1',{exact:true}).count()===0,'主页面仍显示长表单');
  await open().scrollIntoViewIfNeeded();await open().focus();const before=await page.evaluate(()=>scrollY);await open().click();await modal().waitFor();
  await modal().getByLabel('属性名称 1',{exact:true}).fill('草稿名称');
  await modal().getByRole('button',{name:'关闭',exact:true}).click();await modal().waitFor({state:'hidden'});
  check(writes.length===0,'关闭时保存了草稿');check(await open().evaluate(el=>document.activeElement===el),'关闭后焦点未返回按钮');
  const after=await page.evaluate(()=>scrollY);check(Math.abs(after-before)<2,`关闭后正文滚动位置改变：${before} -> ${after}`);
  await open().click();await modal().waitFor();check(await modal().getByLabel('属性名称 1',{exact:true}).inputValue()==='草稿名称','重开丢失草稿');
  await modal().locator('.ant-modal-close').click();await modal().waitFor({state:'hidden'});await open().click();await modal().waitFor();
  check(await modal().getByLabel('属性名称 1',{exact:true}).inputValue()==='草稿名称'&&writes.length===0,'X 关闭丢失或保存草稿');
  await page.waitForFunction(()=>document.activeElement?.getAttribute('aria-label')==='属性分析目标标签');await page.keyboard.press('Escape');await modal().waitFor({state:'hidden'});
  await open().click();await modal().waitFor();check(await modal().getByLabel('属性名称 1',{exact:true}).inputValue()==='草稿名称'&&writes.length===0,'Escape 关闭丢失或保存草稿');
  await modal().getByRole('button',{name:'保存为模板',exact:true}).click();const group=modal().getByRole('group',{name:'保存属性模板'});
  await group.getByLabel('模板名称',{exact:true}).fill('草稿模板');await group.getByRole('button',{name:'保存模板',exact:true}).click();
  await group.waitFor({state:'hidden'});check(templates.length===1&&writes.length===0&&project.label_schema.object_attributes.fields[0].name==='属性1','保存模板修改了项目');
  await modal().locator('button').filter({hasText:'保存属性配置'}).click();await modal().getByText('配置保存失败，请重试',{exact:true}).waitFor();
  check(await modal().getByLabel('属性名称 1',{exact:true}).inputValue()==='草稿名称','失败丢失草稿');
  failSave=false;await modal().locator('button').filter({hasText:'保存属性配置'}).click();await modal().waitFor({state:'hidden'});
  check(writes.length===2&&project.label_schema.object_attributes.fields[0].name==='草稿名称','重试未保存草稿');
  for(const width of [1440,390]){
    await page.setViewportSize({width,height:844});await open().click();await modal().waitFor();
    await page.waitForFunction(()=>document.activeElement?.getAttribute('aria-label')==='属性分析目标标签');
    const layout=await modal().evaluate(element=>{
      const body=element.querySelector('.ant-modal-body'),header=element.querySelector('.ant-modal-header'),footer=element.querySelector('.ant-modal-footer');
      body.scrollTop=body.scrollHeight;const h=header.getBoundingClientRect(),f=footer.getBoundingClientRect();
      return {bodyScrollable:body.scrollHeight>body.clientHeight,scrollTop:body.scrollTop,headerTop:h.top,footerBottom:f.bottom,viewport:innerHeight,pageWidth:document.documentElement.scrollWidth,width:innerWidth};
    });
    check(layout.bodyScrollable&&layout.scrollTop>0&&layout.headerTop>=0&&layout.footerBottom<=layout.viewport+1,`长配置滚动遮挡标题或底部：${JSON.stringify(layout)}`);
    check(layout.pageWidth<=layout.width+1,`页面水平溢出：${JSON.stringify(layout)}`);
    await modal().locator('.ant-modal-body').evaluate(body=>{body.scrollTop=(body.scrollHeight-body.clientHeight)/2;});
    await page.locator('.ant-message').waitFor({state:'hidden'});await page.screenshot({path:`output/playwright/attribute-configuration-modal-${width}.png`,fullPage:false,animations:'disabled'});
    await modal().getByRole('button',{name:'关闭',exact:true}).click();await modal().waitFor({state:'hidden'});
  }
  await page.locator('.ant-message').waitFor({state:'hidden'});await page.screenshot({path:'output/playwright/attribute-configuration-summary.png',fullPage:false,animations:'disabled'});
  await page.getByRole('button',{name:'修改目标标签',exact:true}).click();await modal().waitFor();
  await page.waitForFunction(()=>document.activeElement?.getAttribute('aria-label')==='属性分析目标标签');
  await page.keyboard.press('Escape');await modal().waitFor({state:'hidden'});
  await page.goto(base+`/projects/${id}/attributes?configure=1`);await modal().waitFor();
  await modal().getByLabel('属性名称 1',{exact:true}).fill('再次保存');await modal().locator('button').filter({hasText:'保存属性配置'}).click();
  await modal().waitFor({state:'hidden'});check(!new URL(page.url()).searchParams.has('configure'),'保存后直达参数未清除');
  await page.waitForTimeout(300);check(!await modal().isVisible(),'保存后弹窗重新打开');
  await page.goto(base+`/projects/${freshId}/attributes`);await page.getByRole('button',{name:'配置目标与属性',exact:true}).waitFor();
  check(!await modal().isVisible(),'未指定configure却自动打开');await page.getByRole('button',{name:'配置目标与属性',exact:true}).click();await modal().waitFor();
  if(unknown.length||errors.length)throw Error([...unknown,...errors].join('\n'));
  return {passed:true,draft:true,failed_save_retry:true,template_no_project_write:true,scroll_30_fields:[1440,390],focus_restored:true,shortcut_focus:true,query_auto_open:true,query_cleared:true,unsaved_project_entry:true,unknown_api:unknown,errors};
}
