async page=>{
  const origin='http://127.0.0.1:3000',id='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
  const project={id,project_name:'审计修复验证',task_type:'caption',status:'active',total_assets:0,annotated_count:0,reviewed_count:0,label_schema:{object_attributes:{target_label:'car',fields:[{key:'color',name:'颜色',instruction:'观察颜色',options:[]}]}}};
  const checks=[],unexpected=[],errors=[],modules=[];
  page.on('pageerror',error=>errors.push(error.message));
  page.on('request',request=>{if(request.resourceType()==='script')modules.push(new URL(request.url()).pathname);});
  await page.addInitScript(()=>localStorage.setItem('token','isolated-impeccable-fixes'));
  await page.route(/^https?:\/\/[^/]+\/api(?:\/|\?|$)/,async route=>{
    const req=route.request(),path=new URL(req.url()).pathname;
    if(req.method()==='POST'&&path==='/api/analytics/activity')return route.fulfill({json:{}});
    if(req.method()!=='GET'){unexpected.push(`${req.method()} ${path}`);return route.abort();}
    if(path==='/api/auth/me')return route.fulfill({json:{id:1,username:'fixture',role:'admin',tenant_id:1}});
    if(path===`/api/projects/${id}`)return route.fulfill({json:project});
    if(path==='/api/projects')return route.fulfill({json:{items:[project],total:1,page:1,page_size:10}});
    if(path==='/api/attribute-templates'||path==='/api/model-services'||path===`/api/projects/${id}/work/jobs`)return route.fulfill({json:[]});
    if(path==='/api/model-services/prompt-defaults')return route.fulfill({json:{}});
    if(path==='/api/assets'||path===`/api/projects/${id}/attributes`)return route.fulfill({headers:{'X-Asset-Crop-Depth-Filter':'1'},json:{items:[],total:0}});
    unexpected.push(path);return route.abort();
  });
  const check=(value,message)=>{if(!value)throw Error(message);checks.push(message);};
  await page.setViewportSize({width:1440,height:900});await page.goto(origin+'/projects');
  await page.getByRole('link',{name:`进入项目 ${project.project_name}`,exact:true}).waitFor();
  check(!modules.some(path=>/\/(TrainingPage|DetectionWorkspace|ModelPreviewPage|ObjectAttributes)\.tsx$/.test(path)),'项目列表不提前请求训练、检测、模型评估和属性编辑模块');
  await page.goto(origin+`/projects/${id}/attributes`);
  const open=page.getByRole('button',{name:'查看 / 编辑配置',exact:true});await open.waitFor();
  const measure=async selector=>page.locator(selector).evaluateAll(elements=>elements.filter(e=>e.getBoundingClientRect().width>0).map(e=>{
    const rgb=value=>{const m=value.match(/[\d.]+/g);return m?m.map(Number):[0,0,0,0];};
    const luminance=c=>c.slice(0,3).map(v=>{v/=255;return v<=.04045?v/12.92:((v+.055)/1.055)**2.4;}).reduce((sum,v,i)=>sum+v*[.2126,.7152,.0722][i],0);
    const blend=(fg,bg)=>fg.slice(0,3).map((v,i)=>v*(fg[3]??1)+bg[i]*(1-(fg[3]??1)));
    let node=e,bg=[11,16,27],ancestors=[];while(node){ancestors.push(node);node=node.parentElement;}
    for(const ancestor of ancestors.reverse())bg=blend(rgb(getComputedStyle(ancestor).backgroundColor),bg);
    const fg=blend(rgb(getComputedStyle(e).color),bg),a=luminance(fg),b=luminance(bg);
    return {text:e.textContent?.slice(0,60),ratio:(Math.max(a,b)+.05)/(Math.min(a,b)+.05)};
  }));
  const contrast=await measure('.ant-table-placeholder .ant-table-expanded-row-fixed, .ant-typography-secondary');
  check(contrast.length>0&&contrast.every(item=>item.ratio>=4.5),`空态及帮助文字达到4.5:1：${JSON.stringify(contrast)}`);
  await open.click();const modal=page.getByRole('dialog',{name:'目标与属性配置',exact:true});await modal.waitFor();
  const modalContrast=await measure('.ant-modal .ant-typography-secondary');
  check(modalContrast.length>0&&modalContrast.every(item=>item.ratio>=4.5),'弹窗帮助文字达到4.5:1');
  for(const width of [1440,768,390,320]){
    await page.setViewportSize({width,height:844});await page.waitForTimeout(250);
    const fit=await modal.evaluate(el=>{const header=el.querySelector('.ant-modal-header').getBoundingClientRect(),footer=el.querySelector('.ant-modal-footer').getBoundingClientRect();return document.documentElement.scrollWidth<=innerWidth+1&&header.top>=0&&footer.bottom<=innerHeight+1;});
    check(fit,`${width}px配置弹窗标题/底栏可见且页面无横溢出`);
  }
  await page.screenshot({path:'output/playwright/impeccable-fixed-modal-320.png',animations:'disabled'});
  check(!unexpected.length&&!errors.length,`无异常接口或页面错误：${JSON.stringify({unexpected,errors})}`);
  return {passed:true,checks,contrast,modalContrast,unexpected,errors};
}
