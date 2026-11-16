async page=>{
  await page.unrouteAll({behavior:'ignoreErrors'});page.setDefaultTimeout(10000);
  const base='http://127.0.0.1:3000',projectId='11111111-1111-4111-8111-111111111111';
  const cases=[
    {id:'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',kind:'caption',crop:[400,800],source:[1920,1080]},
    {id:'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',kind:'caption',crop:[800,400],source:[720,1280]},
    {id:'cccccccc-cccc-4ccc-8ccc-cccccccccccc',kind:'detection',crop:[400,800],source:[1920,1080]},
    {id:'dddddddd-dddd-4ddd-8ddd-dddddddddddd',kind:'detection',crop:[800,400],source:[720,1280]},
  ];
  const errors=[],unexpected=[],checks=[];let current=cases[0];
  const assert=(value,message)=>{if(!value)throw Error(message);};
  await page.addInitScript(()=>localStorage.setItem('token','isolated-source-fit'));page.on('pageerror',e=>errors.push(e.message));
  await page.route(/^https?:\/\/[^/]+\/api(?:\/|\?|$)/,async route=>{
    const req=route.request(),url=new URL(req.url()),path=url.pathname,method=req.method();
    if(url.origin!==base){unexpected.push(`${method} ${url}`);return route.abort();}
    if(method==='POST'&&path==='/api/analytics/activity')return route.fulfill({json:{accepted_seconds:0,duplicate:false}});
    if(method!=='GET'){unexpected.push(`${method} ${url}`);return route.abort();}
    if(path==='/api/auth/me')return route.fulfill({json:{id:1,tenant_id:1,username:'fixture',role:'admin'}});
    if(path==='/api/projects/'+projectId)return route.fulfill({json:{id:projectId,project_name:'原图适配',task_type:current.kind,status:'active',label_schema:{labels:['car']}}});
    if(path==='/api/assets/collections'||path==='/api/model-services')return route.fulfill({json:[]});
    if(path==='/api/model-services/prompt-defaults')return route.fulfill({json:{}});
    if(path.endsWith('/attribute-options'))return route.fulfill({json:{}});
    if(path==='/api/annotations')return route.fulfill({json:{items:[{id:'ann-'+current.id,asset_id:current.id,version:1,is_latest:true,review_status:'pending',annotation_source:'manual',annotation_data:current.kind==='caption'?{labels:[],description:''}:{boxes:[{x:10,y:10,width:30,height:30,label:'car'}],negative:false}}],total:1}});
    if(path.startsWith('/api/annotations/assets/')&&path.endsWith('/events'))return route.fulfill({json:[]});
    if(path.endsWith('/analysis-models'))return route.fulfill({json:[]});
    if(path.startsWith('/api/assets/')){
      const id=path.split('/')[3],item=cases.find(c=>c.id===id);if(!item){unexpected.push(path);return route.abort();}
      if(path.endsWith('/content')){const [width,height]=path.endsWith('/source/content')?item.source:item.crop;return route.fulfill({contentType:'image/svg+xml',body:`<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}"><rect width="${width}" height="${height}" fill="#607d9d"/></svg>`});}
      if(path.endsWith('/source'))return route.fulfill({json:{original_name:`完整原图-${id[0]}.png`,width:item.source[0],height:item.source[1],region:[20,30,120,130],content_url:`/api/assets/${id}/source/content`}});
      if(path.endsWith('/neighbors')){const peers=cases.filter(c=>c.kind===item.kind),index=peers.indexOf(item);return route.fulfill({json:{position:index+1,total:2,previous_id:peers[index-1]?.id||null,next_id:peers[index+1]?.id||null}});}
      return route.fulfill({json:{id,project_id:projectId,original_name:`裁剪图-${id[0]}.png`,width:item.crop[0],height:item.crop[1],source_type:'model_crop',annotation_status:'pending'}});
    }
    unexpected.push(method+' '+path);return route.fulfill({status:500,json:{detail:'Unexpected API'}});
  });
  const svg=()=>page.getByRole('img',{name:'原图裁剪位置',exact:true});
  const checkFit=async (item,stage)=>{
    await page.getByText(`完整原图-${item.id[0]}.png`,{exact:false}).waitFor();
    const canvas=svg();await canvas.waitFor();await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
    const m=await canvas.evaluate(element=>{
      const rect=element.getBoundingClientRect(),column=element.closest('.workspace-image-column,.image-annotation-picture'),bounds=column.getBoundingClientRect();
      const after=column.querySelector('.workspace-image-column > .ant-space');const bottom=after?after.getBoundingClientRect().top:bounds.bottom;
      const vb=element.viewBox.baseVal,ctm=element.getScreenCTM();const points=[[vb.x,vb.y],[vb.x+vb.width,vb.y+vb.height]].map(([x,y])=>new DOMPoint(x,y).matrixTransform(ctm));
      return {width:rect.width,height:rect.height,columnWidth:column.clientWidth,availableHeight:bottom-rect.top,points:points.map(p=>({x:p.x,y:p.y})),rect:rect.toJSON()};
    });
    const ratio=item.source[0]/item.source[1];
    assert(Math.abs(m.width/m.height/ratio-1)<.025,`${stage} image distorted: ${JSON.stringify(m)}`);
    assert(m.points.every(p=>p.x>=m.rect.left-1&&p.x<=m.rect.right+1&&p.y>=m.rect.top-1&&p.y<=m.rect.bottom+1),`${stage} image clipped: ${JSON.stringify(m)}`);
    assert(m.width<=m.columnWidth+2&&m.height<=m.availableHeight+2,`${stage} exceeds available area: ${JSON.stringify(m)}`);
    assert(m.columnWidth-m.width<3||m.availableHeight-m.height<3,`${stage} image does not fill available width or height: ${JSON.stringify(m)}`);
    const region=canvas.locator('g rect').first();assert(await region.getAttribute('x')==='20'&&await region.getAttribute('y')==='30'&&await region.getAttribute('width')==='100','source crop coordinates changed');
    const scale=page.getByLabel('图片缩放比例',{exact:true});
    assert((await scale.innerText()).includes('适应')&&(await scale.innerText()).includes('100%'),'initial scale is not labeled as fit');
    assert((await scale.getAttribute('title'))?.includes('适应'),'fit tooltip missing');
    const before=await canvas.getAttribute('viewBox');await page.getByRole('button',{name:'放大图片',exact:true}).click();
    const zoom=await canvas.getAttribute('viewBox');assert(zoom!==before,'zoom did not change view');
    const values=zoom.split(' ').map(Number);assert(Math.abs(values[2]/values[3]/ratio-1)<.001,'zoom distorted aspect');
    assert((await scale.innerText()).includes('120%'),'zoom percentage incorrect');
    const bounds=await canvas.boundingBox();await page.keyboard.down('Control');await page.mouse.move(bounds.x+bounds.width/2,bounds.y+bounds.height/2);await page.mouse.down();await page.mouse.move(bounds.x+bounds.width/2+20,bounds.y+bounds.height/2);await page.mouse.up();await page.keyboard.up('Control');
    assert(await canvas.getAttribute('viewBox')!==zoom,'pan did not move the view');assert((await scale.innerText()).includes('120%'),'pan changed zoom percentage');
    await page.getByRole('button',{name:'适应图片',exact:true}).click();assert(await canvas.getAttribute('viewBox')===before,'fit did not restore source view');assert((await scale.innerText()).includes('适应')&&(await scale.innerText()).includes('100%'),'fit label not restored');
    await page.keyboard.down('Control');await page.mouse.move(bounds.x+bounds.width/2,bounds.y+bounds.height/2);await page.mouse.down();await page.mouse.move(bounds.x+bounds.width/2+20,bounds.y+bounds.height/2);await page.mouse.up();await page.keyboard.up('Control');
    assert(await canvas.getAttribute('viewBox')!==before&&(await scale.innerText()).includes('100%')&&!(await scale.innerText()).includes('适应'),'panned 100% is mislabeled as fitted');
    await page.getByRole('button',{name:'适应图片',exact:true}).click();
    checks.push({kind:item.kind,crop:item.crop,source:item.source,stage,width:m.width,height:m.height,columnWidth:m.columnWidth,availableHeight:m.availableHeight});
  };
  await page.setViewportSize({width:1484,height:1000});
  for(const kind of ['caption','detection']){
    const peers=cases.filter(c=>c.kind===kind);current=peers[0];await page.goto(base+'/annotate/'+current.id);await page.evaluate(()=>sessionStorage.clear());await page.reload();
    const cropName=kind==='detection'?'检测画框区域':'当前标注图片缩放区域';await page.getByRole('img',{name:cropName}).waitFor();
    await page.getByText('原图',{exact:true}).click();await checkFit(current,'desktop');
    await page.getByRole('button',{name:'全屏标注',exact:true}).click();await checkFit(current,'fullscreen');await page.getByRole('button',{name:'退出全屏',exact:true}).click();
    current=peers[1];await page.getByRole(kind==='detection'?'button':'link',{name:'下一张',exact:true}).click();await page.waitForURL('**/annotate/'+current.id+'*');await checkFit(current,'next-desktop');
    await page.getByRole('button',{name:'全屏标注',exact:true}).click();await checkFit(current,'next-fullscreen');await page.getByRole('button',{name:'退出全屏',exact:true}).click();
    await page.getByText('标注切图',{exact:true}).click();current=peers[0];await page.getByRole(kind==='detection'?'button':'link',{name:'上一张',exact:true}).click();await page.getByRole('img',{name:cropName}).waitFor();
    assert(await svg().count()===0,'crop view preference lost on image navigation');
    await page.setViewportSize({width:390,height:844});await page.getByText('原图',{exact:true}).click();await svg().waitFor();
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'mobile page overflow');
    await page.setViewportSize({width:1484,height:1000});
  }
  if(unexpected.length||errors.length)throw Error([...unexpected,...errors].join('\n'));
  return {passed:true,checks,viewPreference:true,sourceCoordinates:true,zoomFit:true,mobileNoOverflow:true};
}
