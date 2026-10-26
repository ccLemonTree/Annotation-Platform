async(page)=>{
  const asset='01ba049a-bc58-4749-848e-c3ce35979269',checks=[];
  const canvas=page.getByRole('img',{name:'检测画框区域'});
  const fit=async(name)=>{
    await canvas.waitFor();
    await page.getByRole('link',{name:'返回 test-img 项目图片',exact:true}).waitFor();
    await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
    const metrics=await canvas.evaluate(svg=>{
      const bounds=svg.getBoundingClientRect(),view=svg.viewBox.baseVal,matrix=svg.getScreenCTM();
      const corners=[[0,0],[view.width,view.height]].map(([x,y])=>new DOMPoint(x,y).matrixTransform(matrix));
      const negative=[...document.querySelectorAll('.ant-checkbox-wrapper')].find(el=>el.textContent.includes('负样本')).getBoundingClientRect();
      return {pageHeight:document.documentElement.scrollHeight,viewport:innerHeight,canvasBottom:bounds.bottom,negativeBottom:negative.bottom,
        imageFits:corners.every(p=>p.x>=bounds.left-1&&p.x<=bounds.right+1&&p.y>=bounds.top-1&&p.y<=bounds.bottom+1)};
    });
    if(metrics.pageHeight>metrics.viewport+1||metrics.canvasBottom>metrics.viewport||metrics.negativeBottom>metrics.viewport||!metrics.imageFits)
      throw Error(name+': workspace requires page scrolling '+JSON.stringify(metrics));
    checks.push(name+' keeps complete image and negative confirmation within viewport');
  };
  for(const [width,height] of [[1366,768],[1692,947],[1280,600]]){
    await page.setViewportSize({width,height});
    await page.goto('http://localhost:3000/annotate/'+asset);
    await fit(width+'x'+height);
  }
  await canvas.focus();await page.getByRole('button',{name:'放大图片',exact:true}).click();
  const zoom=await canvas.getAttribute('viewBox');
  await page.setViewportSize({width:1440,height:900});
  if(await canvas.getAttribute('viewBox')!==zoom)throw Error('Window resize discarded zoom');
  await page.getByRole('button',{name:'适应图片',exact:true}).click();
  await fit('window resize and fit reset');
  const pattern='http://localhost:3000/api/annotations?**';
  await page.route(pattern,async route=>{
    await route.fulfill({json:{items:[{id:'11111111-1111-4111-8111-111111111111',asset_id:asset,is_latest:true,version:1,review_status:'pending',annotation_data:{boxes:Array.from({length:60},(_,i)=>({label:'person',x:i*2,y:i*2,width:20,height:20}))}}],total:1,page:1,page_size:100}});
  });
  try{
    await page.goto('http://localhost:3000/annotate/'+asset);await canvas.waitFor();
    const inspector=page.locator('.workspace-inspector');
    await page.getByRole('heading',{name:'框列表（60）',exact:true}).waitFor();
    const scrollable=await inspector.evaluate(el=>el.scrollHeight>el.clientHeight&&getComputedStyle(el).overflowY==='auto');
    if(!scrollable)throw Error('Long inspector is not independently scrollable');
    await inspector.evaluate(el=>{el.scrollTop=el.scrollHeight;});
    if(await page.evaluate(()=>scrollY)!==0)throw Error('Inspector scrolled the whole page');
    checks.push('long box list scrolls inside inspector while toolbar and canvas stay visible');
  }finally{await page.unroute(pattern);}
  await page.goto('http://localhost:3000/annotate/'+asset);await canvas.waitFor();
  await page.setViewportSize({width:390,height:844});
  if(await page.locator('html').evaluate(el=>el.scrollWidth>el.clientWidth+1))throw Error('Mobile overflow');
  await page.getByRole('button',{name:'快捷键',exact:true}).click();
  await page.getByRole('dialog',{name:'标注快捷键',exact:true}).waitFor();
  await page.keyboard.press('Escape');
  await page.getByRole('dialog',{name:'标注快捷键',exact:true}).waitFor({state:'hidden'});
  await page.locator('.ant-modal-mask').waitFor({state:'hidden'});
  checks.push('mobile stacked layout and shortcuts remain accessible');
  await page.setViewportSize({width:1692,height:947});
  await fit('return from mobile layout');
  await page.screenshot({path:'output/playwright/workspace-viewport-final.png'});
  return {checks,scope:'real images read; long box list mocked; no writes'};
}
