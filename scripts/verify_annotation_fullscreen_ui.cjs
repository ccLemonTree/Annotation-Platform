async page=>{
  await page.unrouteAll({behavior:'ignoreErrors'});page.setDefaultTimeout(10000);
  const base='http://127.0.0.1:3000',projectId='11111111-1111-4111-8111-111111111111';
  const kinds=['attributes','detection','classification','vector'];
  const ids=kinds.map((_,index)=>[`${index+1}aaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa`,`${index+1}bbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb`]);
  const schema={target_label:'car',fields:[{key:'color',name:'颜色',instruction:'可见颜色',options:[]}]};
  let kind='attributes';const writes=[],errors=[],checks=[];
  const check=(value,message)=>{if(!value)throw Error(message);};
  const item=id=>({id,project_id:projectId,original_name:'全屏图片.png',width:id.includes('bbbbbbb')?720:1600,height:id.includes('bbbbbbb')?1080:900,source_type:'upload',annotation_status:'completed'});
  const original=()=>kind==='detection'?{boxes:[{x:100,y:100,width:200,height:180,label:'car'}],description:'原始说明'}:kind==='attributes'?{labels:[],description:'原始说明',attribute_schema:schema,attributes:{color:{value:'白色',reason:'可见'}}}:{labels:kind==='classification'?['car']:[],description:'原始说明'};
  page.on('pageerror',error=>errors.push(error.message));await page.addInitScript(()=>localStorage.setItem('token','isolated-fullscreen'));
  await page.route(base+'/api/**',async route=>{
    const req=route.request(),u=new URL(req.url()),p=u.pathname;
    if(p==='/api/auth/me')return route.fulfill({json:{id:1,tenant_id:1,username:'管理员',role:'admin'}});
    if(p==='/api/analytics/activity')return route.fulfill({json:{}});
    if(p==='/api/projects/'+projectId)return route.fulfill({json:{id:projectId,project_name:'全屏标注验证',task_type:kind==='attributes'?'caption':kind,status:'active',label_schema:{labels:['car'],...(kind==='attributes'?{object_attributes:schema}:{})}}});
    if(p.endsWith('/attribute-options'))return route.fulfill({json:{color:['白色','黑色']}});
    if(p==='/api/assets/collections')return route.fulfill({json:[]});
    if(p.startsWith('/api/assets/')){
      const id=p.split('/')[3],meta=item(id),pair=ids[kinds.indexOf(kind)];
      if(p.endsWith('/content'))return route.fulfill({contentType:'image/svg+xml',body:`<svg xmlns="http://www.w3.org/2000/svg" width="${meta.width}" height="${meta.height}"><rect width="100%" height="100%" fill="#314c74"/></svg>`});
      if(p.endsWith('/neighbors'))return route.fulfill({json:{position:id===pair[0]?1:2,total:2,previous_id:id===pair[1]?pair[0]:null,next_id:id===pair[0]?pair[1]:null}});
      return route.fulfill({json:meta});
    }
    if(p==='/api/annotations'&&req.method()==='GET')return route.fulfill({json:{items:[{id:'ann-'+u.searchParams.get('asset_id'),asset_id:u.searchParams.get('asset_id'),version:1,is_latest:true,annotation_source:'auto',review_status:'pending',annotation_data:original()}],total:1}});
    if(req.method()!=='GET'){writes.push(p);throw Error('Unexpected write '+p);}
    return route.fulfill({json:[]});
  });
  const settle=async canvas=>{await canvas.waitFor();await page.evaluate(()=>new Promise(resolve=>{let left=5;const frame=()=>--left?requestAnimationFrame(frame):resolve();requestAnimationFrame(frame);}));};
  const sidebar=()=>page.locator(kind==='detection'?'.workspace-inspector':'.image-annotation-sidebar');
  await page.setViewportSize({width:1648,height:1000});
  for(const [index,current] of kinds.entries()){
    kind=current;await page.goto(base+'/annotate/'+ids[index][0]);
    const canvas=page.getByRole('img',{name:kind==='detection'?'检测画框区域':'当前标注图片缩放区域',exact:true});await settle(canvas);
    const input=kind==='attributes'?page.getByLabel('颜色属性值',{exact:true}):page.getByLabel(kind==='detection'?'检测图片描述':kind==='classification'?'补充描述（可选）':'图片描述（可选）',{exact:true});
    await input.fill('尚未保存的人工修改');
    await page.getByRole('button',{name:'放大图片',exact:true}).click();const zoom=await canvas.getAttribute('viewBox');
    const normalImage=(await canvas.boundingBox()).height,normalSidebar=(await sidebar().boundingBox()).width;
    await page.getByRole('button',{name:'全屏标注',exact:true}).click();await page.getByRole('button',{name:'退出全屏',exact:true}).waitFor();await settle(canvas);
    const frame=await page.locator('.annotation-stage-fullscreen').boundingBox();
    check(frame.x===0&&frame.y===0&&Math.abs(frame.width-1648)<1&&Math.abs(frame.height-1000)<1,'Fullscreen does not fill viewport');
    check(await page.evaluate(()=>!!document.elementFromPoint(5,5)?.closest('.annotation-stage-fullscreen')),'External navigation remains exposed');
    check(await page.evaluate(()=>[...document.querySelectorAll('.platform-sidebar,.platform-header')].every(element=>getComputedStyle(element).visibility==='hidden')),'Background navigation remains keyboard-focusable');
    check(!await page.getByRole('region',{name:'项目工作流程'}).isVisible(),'Project workflow takes fullscreen space');
    check(await input.inputValue()==='尚未保存的人工修改'&&await canvas.getAttribute('viewBox')===zoom,'Toggle discarded draft/zoom');
    const fullImage=(await canvas.boundingBox()).height,fullSidebar=(await sidebar().boundingBox()).width;
    check(fullImage>normalImage+30,'Fullscreen did not increase image area');
    check(Math.abs(normalSidebar-440)<1&&Math.abs(fullSidebar-440)<1,'Sidebar is not fixed to 440px');
    await page.screenshot({path:`output/playwright/annotation-fullscreen-${kind}.png`,fullPage:true,animations:'disabled'});
    await page.getByRole('button',{name:'快捷键',exact:true}).click();await page.getByRole('dialog').waitFor();await page.keyboard.press('Escape');await page.getByRole('dialog').waitFor({state:'hidden'});
    check(await page.getByRole('button',{name:'退出全屏',exact:true}).isVisible(),'Escape inside modal exited fullscreen');
    await page.locator('.ant-modal-mask').waitFor({state:'hidden'});await canvas.focus();await page.keyboard.press('Escape');
    await page.getByRole('button',{name:'全屏标注',exact:true}).waitFor();check(await input.inputValue()==='尚未保存的人工修改','Escape discarded draft');
    await input.fill(kind==='attributes'?'白色':'原始说明');await page.getByRole('button',{name:'全屏标注',exact:true}).click();
    await page.getByRole(kind==='detection'?'button':'link',{name:'下一张',exact:true}).click();await page.waitForURL('**/annotate/'+ids[index][1]+'*');await settle(canvas);
    check(await page.getByRole('button',{name:'退出全屏',exact:true}).isVisible(),'Next image exited fullscreen');
    check(Math.abs((await sidebar().boundingBox()).width-440)<1,'Portrait image changed sidebar width');
    await page.getByRole(kind==='detection'?'button':'link',{name:'上一张',exact:true}).click();await page.waitForURL('**/annotate/'+ids[index][0]+'*');await settle(canvas);
    check(await page.getByRole('button',{name:'退出全屏',exact:true}).isVisible(),'Previous image exited fullscreen');
    await page.getByRole('button',{name:'退出全屏',exact:true}).click();await settle(canvas);
    check(Math.abs((await sidebar().boundingBox()).width-440)<1,'Exit fullscreen changed sidebar width');
    check(await page.evaluate(()=>document.body.style.overflow!=='hidden'),'Exiting left scroll locked');
    checks.push({kind,normalImage:Math.round(normalImage),fullImage:Math.round(fullImage),sidebar:fullSidebar});
  }
  await page.setViewportSize({width:420,height:950});await page.getByRole('button',{name:'全屏标注',exact:true}).click();
  check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'Mobile fullscreen overflows');await page.getByRole('button',{name:'退出全屏',exact:true}).click();
  check(errors.length===0,errors.join('\n'));check(writes.length===0,'Fullscreen automatically wrote annotations');
  return {checks,modalEscapeProtected:true,canvasEscapeExits:true,fullscreenPersistsAcrossImages:true,mobile:true,annotationWrites:0,errors};
}
