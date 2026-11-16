async page=>{
  const base='http://127.0.0.1:3000',writes=[];
  let config={classification_enabled:true,base_url:'http://model.test/v1',model:'test-vision',min_long_edge:720,
    scenes:['工厂'],scene_groups:[{name:'生产作业',scenes:['工厂']}],scene_prompt:'已保存的设备场景分类规则',revision:3};
  const defaults={scene:'系统默认场景分类指令',caption:'默认描述',review:'默认审核',match_review:'默认匹配',training_analysis:'默认训练分析'};
  const check=(value,message)=>{if(!value)throw Error(message);};
  await page.unrouteAll({behavior:'ignoreErrors'});page.setDefaultTimeout(6000);
  await page.addInitScript(()=>localStorage.setItem('token','isolated-device-prompt'));
  await page.route(base+'/api/**',async route=>{
    const request=route.request(),path=new URL(request.url()).pathname;
    if(path==='/api/images/ingest-settings'){
      if(request.method()==='PUT'){config={...request.postDataJSON(),revision:config.revision+1};writes.push(config);}
      return route.fulfill({json:config});
    }
    if(request.method()!=='GET')throw Error('Unexpected mutation '+path);
    if(path==='/api/model-services/prompt-defaults')return route.fulfill({json:defaults});
    if(path==='/api/projects')return route.fulfill({json:{items:[],total:0}});
    if(path==='/api/images/collections')return route.fulfill({json:[]});
    if(path==='/api/auth/me')return route.fulfill({json:{id:1,role:'admin',tenant_id:1}});
    let data=[];
    if(path.endsWith('/device-scenes/config'))data=config;
    else if(path.endsWith('/devices'))data={items:[],total:0};
    else if(!path.endsWith('/managed-platforms')&&!path.endsWith('/tasks')&&!path.endsWith('/device-capture/schedules'))throw Error('Unexpected API '+path);
    return route.fulfill({json:{code:0,data}});
  });
  const open=async()=>{await page.getByRole('button',{name:'场景分类设置',exact:true}).click();await page.getByRole('dialog',{name:'设备场景分类设置'}).waitFor();};
  const editor=()=>page.getByRole('dialog',{name:'设备场景分类提示词',exact:true});
  const prompt=()=>editor().getByRole('textbox',{name:'设备场景分类提示词',exact:true});
  const openPrompt=async()=>{await page.getByRole('button',{name:'编辑设备场景提示词',exact:true}).click();await prompt().waitFor();};
  await page.setViewportSize({width:1440,height:1000});await page.goto(base+'/smarteye/index.html?project_id=fixture');
  await open();check(await prompt().count()===0,'Scene prompt still inline');await openPrompt();check(await prompt().inputValue()===config.scene_prompt,'Saved scene prompt not shown');
  await prompt().fill('');check(await editor().getByRole('button',{name:'应用提示词',exact:true}).isDisabled(),'Empty required prompt may be applied');check(writes.length===0,'Empty prompt was saved');
  await prompt().fill('按本项目作业区域标准判断场景，遮挡时不要猜测。');
  await editor().getByRole('button',{name:'应用提示词',exact:true}).click();check(writes.length===0,'Applying prompt saved settings');
  await page.getByRole('button',{name:'保存分类设置',exact:true}).click();await page.getByRole('dialog',{name:'设备场景分类设置'}).waitFor({state:'hidden'});
  check(writes.length===1&&writes[0].scene_prompt==='按本项目作业区域标准判断场景，遮挡时不要猜测。','Custom prompt not submitted');
  check(writes[0].base_url==='http://model.test/v1'&&writes[0].min_long_edge===720,'Editing prompt changed unrelated config');
  await open();await openPrompt();check(await prompt().inputValue()===config.scene_prompt,'Reopen lost saved prompt');
  await editor().getByRole('button',{name:'恢复默认提示词',exact:true}).click();
  check(await prompt().inputValue()===defaults.scene,'Restore did not fill system default');check(writes.length===1,'Restore saved without explicit save');
  await page.screenshot({path:'output/playwright/device-scene-prompt-inline-desktop.png',fullPage:true,animations:'disabled'});
  await page.setViewportSize({width:420,height:900});await page.screenshot({path:'output/playwright/device-scene-prompt-inline-narrow.png',fullPage:true,animations:'disabled'});
  check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'Device settings overflow narrow screen');
  await editor().getByRole('button',{name:'应用提示词',exact:true}).click();await page.getByRole('button',{name:'保存分类设置',exact:true}).click();await page.getByRole('dialog',{name:'设备场景分类设置'}).waitFor({state:'hidden'});
  check(writes.length===2&&writes[1].scene_prompt===defaults.scene,'Default prompt not explicitly persisted');
  return {checks:9,configuration_writes:writes.length,classification_jobs_started:0};
}
