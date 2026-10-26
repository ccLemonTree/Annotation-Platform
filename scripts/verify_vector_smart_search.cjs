async page=>{
  await page.unrouteAll();page.setDefaultTimeout(10000);
  const origin='http://127.0.0.1:3000',checks=[],requests=[],writes=[],errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  const source=await(await page.request.get(origin+'/src/components/VectorAnnotation.tsx')).text();
  const react=source.match(/["'](\/node_modules\/\.vite\/deps\/react\.js\?[^"']+)["']/)[1],router=source.match(/["'](\/node_modules\/\.vite\/deps\/react-router-dom\.js\?[^"']+)["']/)[1];
  const project={id:'vector-fixture',project_name:'智搜标注隔离测试',task_type:'vector',label_schema:{labels:[],annotation_scope:'crops'}};
  const meta={platform_id:'p1',platform_name:'杭州移动消防平台',region:[250,200,750,600],original_width:1000,original_height:800,record:{deviceName:'摄像头1',timestamp:'2026-10-23 12:00:00'}};
  const hit={asset_id:'crop-1',original_name:'目标切图.png',source_type:'model_crop',score:.8,judgment:'unknown',smart_search:meta};
  const history=[];
  await page.route('**/src/App.tsx*',route=>route.fulfill({contentType:'application/javascript',body:`import React from '${react}';import{BrowserRouter,useSearchParams}from'${router}';import Provider from '/src/ui/PlatformProvider.tsx';import Vector from '/src/components/VectorAnnotation.tsx';function Test(){const[p]=useSearchParams();return React.createElement(Vector,{project:${JSON.stringify(project)},mode:p.get('mode')==='image'?'image':'text'});}export default function App(){return React.createElement(Provider,null,React.createElement(BrowserRouter,null,React.createElement(Test)));}`}));
  await page.route('**/api/**',async route=>{
    const req=route.request(),path=new URL(req.url()).pathname;
    if(!path.startsWith('/api/'))return route.continue();
    if(path==='/api/model-services')return route.fulfill({json:[]});
    if(path.endsWith('/smart-search/platforms'))return route.fulfill({json:[{id:'p1',name:'杭州移动消防平台'}]});
    if(path.endsWith('/matches')&&req.method()==='GET')return route.fulfill({json:history});
    if(path.endsWith('/smart-search')&&req.method()==='POST'){
      const body=req.postDataJSON();requests.push(body);
      const query={id:'q'+requests.length,version:1,query:{source:'smart_search',platform_name:'杭州移动消防平台',...(body.text?{text:body.text}:{query_asset_id:'reference'})},space:{source:'smart_search',revision:1,failures:[{index:2,error:'图片或目标框无法读取'}]},hits:[structuredClone(hit)]};
      history.unshift(query);return route.fulfill({json:query});
    }
    if(req.method()==='PUT'&&path.includes('/matches/')){
      const body=req.postDataJSON();writes.push(body);const query=history.find(q=>path.endsWith('/'+q.id));
      if(!query||query.version!==body.base_version)throw Error('stale judgment');
      query.version++;query.hits[0].judgment=body.judgment;return route.fulfill({json:query});
    }
    if(path.includes('/source/'))return route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="800"><rect width="1000" height="800" fill="#555"/><rect x="250" y="200" width="500" height="400" fill="#e34"/></svg>'});
    if(path.endsWith('/content'))return route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="500" height="400"><rect width="500" height="400" fill="#e34"/></svg>'});
    throw Error('Unexpected request '+req.method()+' '+path);
  });
  try{
    await page.setViewportSize({width:1440,height:1100});await page.goto(origin+'/annotate/project/vector-fixture?mode=text');
    await page.getByRole('combobox',{name:'智搜平台',exact:true}).waitFor();
    await page.getByLabel('匹配查询文本',{exact:true}).fill('人');
    await page.getByRole('button',{name:'检索并标注',exact:true}).click();
    await page.getByText('当前查询：人 · 杭州移动消防平台 · 1 个结果',{exact:true}).waitFor();
    if(requests.length!==1||requests[0].text!=='人'||requests[0].platform_id!=='p1')throw Error('Text request mismatch');
    const result=page.locator('#match-crop-1');await result.getByRole('img',{name:'目标切图.png',exact:true}).waitFor();
    await page.getByText('1 张图片下载或裁剪失败，其他结果可正常标注',{exact:true}).waitFor();
    await result.getByRole('radio',{name:'正确',exact:true}).check();
    await page.waitForFunction(()=>document.querySelector('#match-crop-1 .ant-radio-checked input')?.value==='correct');
    if(writes.length!==1||writes[0].judgment!=='correct')throw Error('Direct crop judgment failed');
    checks.push('Text search uses selected platform without a local embedding model; cropped cards support immediate judgment; partial failures are visible');
    await page.screenshot({path:'output/playwright/vector-smart-text.png',fullPage:true,animations:'disabled'});
    await result.getByRole('button',{name:'用此图搜图',exact:true}).click();
    const modal=page.getByRole('dialog',{name:'框选目标，以图搜图',exact:true});await modal.waitFor();
    await modal.getByRole('img',{name:'检测画框区域',exact:true}).waitFor();
    await page.screenshot({path:'output/playwright/vector-smart-crop.png',animations:'disabled'});
    await modal.getByRole('button',{name:'重新框选',exact:true}).click();
    const canvas=modal.getByRole('img',{name:'检测画框区域',exact:true});
    const points=await canvas.evaluate(svg=>{const m=svg.getScreenCTM();return [new DOMPoint(100,100).matrixTransform(m),new DOMPoint(300,260).matrixTransform(m)].map(p=>({x:p.x,y:p.y}));});
    await page.mouse.move(points[0].x,points[0].y);await page.mouse.down();await page.mouse.move(points[1].x,points[1].y,{steps:8});await page.mouse.up();
    await modal.getByRole('button',{name:'使用此框搜图',exact:true}).click();
    await page.getByText('当前查询：图片查询 · 杭州移动消防平台 · 1 个结果',{exact:true}).waitFor();
    const imageRequest=requests[1];
    if(imageRequest.reference_query_id!=='q1'||imageRequest.reference_asset_id!=='crop-1'||imageRequest.text)throw Error('Image reference mismatch');
    const box=imageRequest.region;
    if(Math.abs(box.x-100)>1||Math.abs(box.y-100)>1||Math.abs(box.width-200)>1||Math.abs(box.height-160)>1)throw Error('Drawing did not use original pixels '+JSON.stringify(box));
    if(!page.url().includes('mode=image'))throw Error('Image mode not selected');
    checks.push('Result card opens an authenticated original only for drawing; selected pixels submit a picture query and retain a cropped query reference');
    await page.evaluate(()=>document.activeElement?.blur());await page.keyboard.press('2');
    await page.waitForFunction(()=>document.querySelector('#match-crop-1 .ant-radio-checked input')?.value==='incorrect');
    if(writes.at(-1).judgment!=='incorrect')throw Error('Image-mode shortcut failed');
    checks.push('Image-search cards retain 1/2/3 judgment shortcuts');
    await page.setViewportSize({width:390,height:844});await page.screenshot({path:'output/playwright/vector-smart-mobile.png',fullPage:true,animations:'disabled'});
    const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>window.innerWidth+1);
    if(overflow)throw Error('Mobile horizontal overflow');
    if(errors.length)throw Error(errors.join('\n'));
    return {checks,fixtureSearches:requests.length,fixtureJudgments:writes.length,businessWrites:0};
  }finally{await page.goto('about:blank');await page.unrouteAll({behavior:'ignoreErrors'});}
}
