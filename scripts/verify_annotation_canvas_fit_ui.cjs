async page=>{
  await page.unrouteAll({behavior:'ignoreErrors'});page.setDefaultTimeout(10000);
  const base='http://127.0.0.1:3000',projectId='11111111-1111-4111-8111-111111111111';
  const cases=[['aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa','caption',720,1080],['bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb','caption',1600,500],['cccccccc-cccc-4ccc-8ccc-cccccccccccc','detection',1920,1080],['dddddddd-dddd-4ddd-8ddd-dddddddddddd','detection',720,1280]];
  const schema={target_label:'car',fields:[{key:'color',name:'颜色',instruction:'核对颜色',options:['黑','白']}]};
  const check=(value,message)=>{if(!value)throw Error(message);};const errors=[],writes=[],checks=[];
  let current=cases[0];
  page.on('pageerror',e=>errors.push(e.message));await page.addInitScript(()=>localStorage.setItem('token','isolated-canvas-fit'));
  await page.route(base+'/api/**',async route=>{
    const req=route.request(),u=new URL(req.url()),p=u.pathname;
    if(p==='/api/auth/me')return route.fulfill({json:{id:1,tenant_id:1,username:'管理员',role:'admin'}});
    if(p==='/api/analytics/activity')return route.fulfill({json:{}});
    if(p==='/api/projects/'+projectId)return route.fulfill({json:{id:projectId,project_name:'比例适配测试',task_type:current[1],status:'active',label_schema:{labels:['car'],...(current[1]==='caption'?{object_attributes:schema}:{})}}});
    if(p.endsWith('/attribute-options'))return route.fulfill({json:{}});
    if(p==='/api/assets/collections')return route.fulfill({json:[]});
    if(p.startsWith('/api/assets/')){
      if(p.endsWith('/content'))return route.fulfill({contentType:'image/svg+xml',body:`<svg xmlns="http://www.w3.org/2000/svg" width="${current[2]}" height="${current[3]}"><rect width="100%" height="100%" fill="#314c74"/><path d="M0 0 L${current[2]} ${current[3]}" stroke="#a1afc4"/><rect x="1" y="1" width="${current[2]-2}" height="${current[3]-2}" fill="none" stroke="#ff9393" stroke-width="2"/></svg>`});
      if(p.endsWith('/source'))return route.fulfill({json:{original_name:'原图.png',width:2400,height:1600,region:[200,300,800,900],content_url:'/api/assets/'+current[0]+'/content'}});
      if(p.endsWith('/neighbors'))return route.fulfill({json:{position:1,total:1,previous_id:null,next_id:null}});
      return route.fulfill({json:{id:current[0],project_id:projectId,original_name:'比例适配.png',width:current[2],height:current[3],source_type:current[1]==='caption'?'model_crop':'upload',annotation_status:'completed'}});
    }
    if(p==='/api/annotations'&&req.method()==='GET')return route.fulfill({json:{items:[{id:'ann-'+current[0],asset_id:current[0],version:1,is_latest:true,annotation_source:'auto',review_status:'pending',annotation_data:current[1]==='caption'?{labels:[],description:'',attribute_schema:schema,attributes:{color:{value:'白',reason:'车身可见'}}}:{boxes:[{x:100,y:100,width:160,height:120,label:'car'}],negative:false}}],total:1}});
    if(p.startsWith('/api/annotations/ann-')&&req.method()==='PUT'){
      writes.push(req.postDataJSON());return route.fulfill({json:{id:'saved',asset_id:current[0],version:2,is_latest:true,review_status:'pending',annotation_data:req.postDataJSON().annotation_data}});
    }
    if(req.method()!=='GET')throw Error('Unexpected write '+p);
    return route.fulfill({json:[]});
  });
  const metrics=async canvas=>canvas.evaluate(svg=>{
    const b=svg.getBoundingClientRect(),v=svg.viewBox.baseVal,m=svg.getScreenCTM();
    const points=[[0,0],[v.width,v.height]].map(([x,y])=>new DOMPoint(x,y).matrixTransform(m));
    const parent=svg.parentElement.getBoundingClientRect();
    return {x:b.x,y:b.y,width:b.width,height:b.height,centerOffset:(b.left+b.right-parent.left-parent.right)/2,visible:points.every(p=>p.x>=b.left-1&&p.x<=b.right+1&&p.y>=b.top-1&&p.y<=b.bottom+1),pageWidth:document.documentElement.scrollWidth,viewportWidth:innerWidth,pageHeight:document.documentElement.scrollHeight,viewportHeight:innerHeight};
  });
  for(const viewport of [[1648,950],[1366,768],[1280,600]]){
    await page.setViewportSize({width:viewport[0],height:viewport[1]});
    for(const item of cases){
      current=item;await page.goto(base+'/annotate/'+current[0]);
      const canvas=page.getByRole('img',{name:current[1]==='caption'?'当前标注图片缩放区域':'检测画框区域',exact:true});await canvas.waitFor();
      await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
      const m=await metrics(canvas),expected=current[2]/current[3];
      check(Math.abs(m.centerOffset)<2,'Image is not centered: '+JSON.stringify({task:current[1],size:current.slice(2),offset:m.centerOffset}));
      const inspector=page.locator(current[1]==='caption'?'.image-annotation-sidebar':'.workspace-inspector');
      check(Math.abs((await inspector.boundingBox()).width-440)<1,'Inspector changes width with image: '+JSON.stringify({task:current[1],size:current.slice(2),width:(await inspector.boundingBox()).width}));
      check(Math.abs(m.width/m.height/expected-1)<.02,'Canvas has large letterboxing: '+JSON.stringify({task:current[1],size:current.slice(2),viewport,m}));
      check(m.visible,'Image cropped at fit: '+JSON.stringify({task:current[1],size:current.slice(2),viewport,m,viewBox:await canvas.getAttribute('viewBox')}));check(m.pageWidth<=m.viewportWidth+1&&(viewport[1]<700||m.pageHeight<=m.viewportHeight+1),'Desktop page overflows: '+JSON.stringify(m));
      if(viewport[1]<700)check(m.height>=158,'Short window collapsed image');
      const before=await canvas.getAttribute('viewBox');await page.getByRole('button',{name:'放大图片',exact:true}).click();check(await canvas.getAttribute('viewBox')!==before,'Zoom did not work');
      const zoom=await canvas.getAttribute('viewBox');await page.setViewportSize({width:viewport[0]-30,height:viewport[1]});check(await canvas.getAttribute('viewBox')===zoom,'Resize reset zoom');
      await page.getByRole('button',{name:'适应图片',exact:true}).click();check(await canvas.getAttribute('viewBox')===before,'Fit reset changed coordinates');
      await page.setViewportSize({width:viewport[0],height:viewport[1]});
      if(viewport[0]===1648){
        await page.getByRole('button',{name:'全屏标注',exact:true}).click();await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
        check(Math.abs((await metrics(canvas)).centerOffset)<2,'Fullscreen image is not centered');
        await page.getByRole('button',{name:'退出全屏',exact:true}).click();await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
      }
      if(viewport[0]===1648)await page.screenshot({path:`output/playwright/canvas-fit-${current[1]}-${current[2]}.png`,fullPage:true,animations:'disabled'});
      if(viewport[0]===1648&&current[1]==='detection'&&current[2]===1920){
        const points=await canvas.evaluate(svg=>[[300,300],[380,360]].map(([x,y])=>{const p=new DOMPoint(x,y).matrixTransform(svg.getScreenCTM());return {x:p.x,y:p.y};}));
        await page.mouse.move(points[0].x,points[0].y);await page.mouse.down();await page.mouse.move(points[1].x,points[1].y);await page.mouse.up();
        await canvas.click({position:{x:5,y:5}});check(await canvas.isVisible(),'Click changed detection image view');
        await page.getByText('原图',{exact:true}).click();const source=page.getByRole('img',{name:'原图裁剪位置'});await source.waitFor();
        const rect=source.locator('g rect').first();check(await rect.getAttribute('x')==='200'&&await rect.getAttribute('y')==='300','Source region is incorrect');
        const bounds=await source.boundingBox();await page.mouse.move(bounds.x+30,bounds.y+30);await page.mouse.down();await page.mouse.move(bounds.x+100,bounds.y+100);await page.mouse.up();
        check(await source.locator('g').count()===1,'Source view accepted drawing');
        await page.getByText('标注图片',{exact:true}).click();await canvas.waitFor();check(await canvas.locator('g').count()===2,'Switching views lost draft boxes');
        await page.getByRole('button',{name:'保存标注',exact:true}).click();
        await page.waitForFunction(()=>!document.body.textContent.includes('未保存修改'));
        const boxes=writes.at(-1)?.annotation_data.boxes;check(boxes?.length===2,'Fitted canvas did not add box');
        check(['x','y','width','height'].every((key,index)=>Math.abs(boxes[1][key]-[300,300,80,60][index])<1),'Fitted canvas changed pixel coordinates');
        check(boxes[0].x===100&&boxes[0].y===100&&boxes[0].width===160&&boxes[0].height===120,'Fitted canvas changed existing box');
      }
      checks.push({task:current[1],image:current.slice(2),viewport,canvas:[Math.round(m.width),Math.round(m.height)]});
    }
  }
  await page.setViewportSize({width:420,height:950});await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'Mobile overflow: '+JSON.stringify(await page.evaluate(()=>[...document.querySelectorAll('*')].filter(el=>el.getBoundingClientRect().right>innerWidth+1).slice(0,8).map(el=>({tag:el.tagName,cls:el.className,width:el.getBoundingClientRect().width})))));
  check(errors.length===0,errors.join('\n'));check(writes.length===1,'Unexpected annotation writes');
  return {checks,mockedAnnotationWrites:writes.length,coordinatesPreserved:true,errors};
}
