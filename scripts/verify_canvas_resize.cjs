async page=>{
  const origin='http://127.0.0.1:3000',checks=[];
  await page.unrouteAll();page.setDefaultTimeout(8000);await page.setViewportSize({width:1200,height:900});
  const source=await(await page.request.get(origin+'/src/components/DetectionCanvas.tsx')).text();
  const react=source.match(/["'](\/node_modules\/\.vite\/deps\/react\.js\?[^"']+)["']/)[1];
  await page.route('**/src/App.tsx*',route=>route.fulfill({contentType:'application/javascript',body:`
    import React from '${react}';import Provider from '/src/ui/PlatformProvider.tsx';const{useState}=React;
    import Canvas from '/src/components/DetectionCanvas.tsx';import Crop from '/src/components/SmartSearchCrop.tsx';
    import{draftHistory,changeDraft,undoDraft,redoDraft}from'/src/components/detectionEditor.ts';
    const boxes=[{x:160,y:120,width:240,height:160,label:'目标'},{x:100,y:60,width:400,height:300,label:'外层'}];
    export default function App(){const[h,setH]=useState(()=>draftHistory({boxes,negative:false})),[selected,setSelected]=useState(0),[locked,setLocked]=useState(false),[commits,setCommits]=useState(0),[crop,setCrop]=useState(false),[region,setRegion]=useState(null);
    return React.createElement(Provider,null,React.createElement('main',{style:{width:900,padding:20}},
      React.createElement('button',{onClick:()=>{setH(draftHistory({boxes,negative:false}));setSelected(0);setCommits(0);}},'重置测试框'),
      React.createElement('button',{onClick:()=>setH(undoDraft)},'测试撤销'),React.createElement('button',{onClick:()=>setH(redoDraft)},'测试重做'),
      React.createElement('button',{onClick:()=>setLocked(v=>!v)},'切换只读'),React.createElement('button',{onClick:()=>setCrop(true)},'测试搜图'),
      React.createElement('output',{'data-testid':'commits'},commits),React.createElement('output',{'data-testid':'region'},JSON.stringify(region)),
      React.createElement(Canvas,{image:'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jUKcAAAAASUVORK5CYII=',width:640,height:480,boxes:h.present.boxes,selected,label:'目标',tool:'draw',disabled:locked,onSelect:setSelected,onDragging:()=>{},onChange:rows=>{setCommits(n=>n+1);setH(v=>changeDraft(v,{boxes:rows,negative:false}));}}),
      crop&&React.createElement(Crop,{projectId:'fixture',queryId:'query',hit:{asset_id:'asset',smart_search:{region:[160,120,400,280],original_width:640,original_height:480}},onCancel:()=>setCrop(false),onSearch:value=>{setRegion(value.region);setCrop(false);}})));}` }));
  let apiReads=0;
  await page.route(origin+'/api/**',route=>{
    if(route.request().method()!=='GET'||!route.request().url().endsWith('/matches/query/source/asset'))throw Error('Unexpected fixture request');
    apiReads++;return route.fulfill({contentType:'image/png',body:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jUKcAAAAASUVORK5CYII=','base64')});
  });
  const canvas=page.getByRole('img',{name:'检测画框区域'}).first();
  const original={x:160,y:120,width:240,height:160};
  const geometry=()=>canvas.locator('g').first().locator('rect').first().evaluate(el=>Object.fromEntries(['x','y','width','height'].map(k=>[k,Number(el.getAttribute(k))])));
  const same=(a,b)=>Object.keys(b).every(k=>Math.abs(a[k]-b[k])<.1);
  const ensure=(ok,why)=>{if(!ok)throw Error(why);checks.push(why);};
  const reset=()=>page.getByRole('button',{name:'重置测试框',exact:true}).click();
  const point=(svg,x,y)=>svg.evaluate((el,p)=>{const q=el.createSVGPoint();q.x=p.x;q.y=p.y;const t=q.matrixTransform(el.getScreenCTM());return{x:t.x,y:t.y};},{x,y});
  const drag=async(svg,direction,dx,dy,cancel=false)=>{
    const handle=svg.locator('[data-resize="'+direction+'"]'),bounds=await handle.boundingBox();
    const delta=await svg.evaluate((el,p)=>{const m=el.getScreenCTM();return{x:m.a*p.dx+m.c*p.dy,y:m.b*p.dx+m.d*p.dy};},{dx,dy});
    const x=bounds.x+bounds.width/2,y=bounds.y+bounds.height/2;
    await page.mouse.move(x,y);await page.mouse.down();await page.mouse.move(x+delta.x,y+delta.y,{steps:4});
    if(cancel)await page.keyboard.press('Escape');await page.mouse.up();
  };
  try{
    await page.goto(origin+'/');await canvas.waitFor();
    ensure(await canvas.locator('[data-resize]').count()===8,'selected box has eight resize handles');
    for(const direction of ['nw','n','ne','e','se','s','sw','w']){
      await reset();await drag(canvas,direction,20,15);
      const expected={x:160+(direction.includes('w')?20:0),y:120+(direction.includes('n')?15:0),width:240+(direction.includes('e')?20:direction.includes('w')?-20:0),height:160+(direction.includes('s')?15:direction.includes('n')?-15:0)};
      ensure(same(await geometry(),expected),'resize '+direction+' changes only the intended edges even with overlapping boxes');
      ensure(await page.getByTestId('commits').textContent()==='1','resize '+direction+' commits once');
      await page.getByRole('button',{name:'测试撤销',exact:true}).click();ensure(same(await geometry(),original),'undo '+direction);
      await page.getByRole('button',{name:'测试重做',exact:true}).click();ensure(same(await geometry(),expected),'redo '+direction);
    }
    await reset();await page.getByRole('button',{name:'放大图片',exact:true}).click();await drag(canvas,'nw',20,15);
    ensure(same(await geometry(),{x:180,y:135,width:220,height:145}),'zoomed resizing uses original pixel coordinates');
    await page.getByRole('button',{name:'适应图片',exact:true}).click();await reset();await drag(canvas,'nw',-300,-200);
    ensure(same(await geometry(),{x:0,y:0,width:400,height:280}),'top and left edges stop at image bounds');
    await reset();await drag(canvas,'se',500,400);ensure(same(await geometry(),{x:160,y:120,width:480,height:360}),'bottom and right edges stop at image bounds');
    await reset();await drag(canvas,'nw',300,250);ensure(same(await geometry(),{x:399,y:279,width:1,height:1}),'crossing opposite edges preserves a one-pixel minimum');
    await reset();await drag(canvas,'nw',20,15,true);ensure(same(await geometry(),original)&&await page.getByTestId('commits').textContent()==='0','Escape cancels resizing without a committed change');
    await reset();const start=await point(canvas,280,200),end=await point(canvas,300,215);await page.mouse.move(start.x,start.y);await page.mouse.down();await page.mouse.move(end.x,end.y,{steps:3});await page.mouse.up();
    ensure(same(await geometry(),{...original,x:180,y:135}),'dragging inside still moves the whole box');
    await reset();const corner=await point(canvas,160,120),drawEnd=await point(canvas,220,180);
    await page.keyboard.down('Shift');await page.mouse.move(corner.x,corner.y);await page.mouse.down();await page.mouse.move(drawEnd.x,drawEnd.y,{steps:3});await page.mouse.up();await page.keyboard.up('Shift');
    ensure(await canvas.locator('g').count()===3&&same(await geometry(),original),'Shift on a handle still draws a new box without resizing the old one');
    await reset();const view=await canvas.getAttribute('viewBox');await page.keyboard.down('Control');await page.mouse.move(corner.x,corner.y);await page.mouse.down();await page.mouse.move(corner.x+30,corner.y+20,{steps:3});await page.mouse.up();await page.keyboard.up('Control');
    ensure(await canvas.getAttribute('viewBox')!==view&&same(await geometry(),original),'Ctrl on a handle pans without changing box geometry');
    await page.getByRole('button',{name:'适应图片',exact:true}).click();
    await page.getByRole('button',{name:'切换只读',exact:true}).click();ensure(await canvas.locator('[data-resize]').count()===0,'read-only boxes have no resize handles');
    await page.getByRole('button',{name:'测试搜图',exact:true}).click();const cropCanvas=page.getByRole('dialog').getByRole('img',{name:'检测画框区域'});await cropCanvas.waitFor();
    await page.getByRole('dialog').evaluate(el=>Promise.all(el.getAnimations({subtree:true}).map(animation=>animation.finished.catch(()=>{}))));
    await drag(cropCanvas,'nw',20,15);await page.getByRole('button',{name:'使用此框搜图',exact:true}).click();
    const submitted=JSON.parse(await page.getByTestId('region').textContent());
    if(!same(submitted,{x:180,y:135,width:220,height:145})||apiReads!==1)throw Error('Unexpected crop result '+JSON.stringify({submitted,apiReads}));
    checks.push('smart-search crop submits the resized original-pixel region');
    await page.screenshot({path:'output/playwright/canvas-resize.png'});
    return{passed:true,checks,business_writes:0};
  }finally{await page.goto('about:blank');await page.unrouteAll();}
}
