async page=>{
  await page.unrouteAll();page.setDefaultTimeout(8000);
  const origin='http://127.0.0.1:3000',checks=[],writes=[];
  const source=await(await page.request.get(origin+'/src/components/VectorAnnotation.tsx')).text();
  const react=source.match(/["'](\/node_modules\/\.vite\/deps\/react\.js\?[^"']+)["']/)[1],router=source.match(/["'](\/node_modules\/\.vite\/deps\/react-router-dom\.js\?[^"']+)["']/)[1];
  const project={id:'vector-project',project_name:'向量快捷键测试',task_type:'vector',label_schema:{labels:[]}};
  const query={id:'query-1',version:1,query:{text:'穿黑色衣服的人'},space:{revision:1},hits:Array.from({length:3},(_,i)=>({asset_id:'hit-'+i,original_name:'vector-'+i+'.jpg',source_type:'upload',score:.9-i*.1,judgment:'unknown',description:'图片描述',labels:[]}))};
  const app='**/src/App.tsx*',api='**/api/**';
  await page.route(app,route=>route.fulfill({contentType:'application/javascript',body:`import React from '${react}';import{BrowserRouter,useSearchParams}from'${router}';import Provider from '/src/ui/PlatformProvider.tsx';import Vector from '/src/components/VectorAnnotation.tsx';function Test(){const[p]=useSearchParams();return React.createElement(Vector,{project:${JSON.stringify(project)},mode:p.get('mode')==='image'?'image':'text'});}export default function App(){return React.createElement(Provider,null,React.createElement(BrowserRouter,null,React.createElement(Test)));}`}));
  await page.route(api,async route=>{
    const req=route.request(),path=new URL(req.url()).pathname;
    if(!path.startsWith('/api/'))return route.continue();
    if(path==='/api/model-services')return route.fulfill({json:[]});
    if(path.endsWith('/content'))return route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="640" height="480"/>'});
    if(path==='/api/projects/vector-project/matches')return route.fulfill({json:[query]});
    if(path==='/api/projects/vector-project/matches/query-1'&&req.method()==='PUT'){
      const body=req.postDataJSON();writes.push(body);
      if(body.base_version!==query.version)throw Error('Stale match version');
      await new Promise(resolve=>setTimeout(resolve,200));query.version++;query.hits.find(h=>h.asset_id===body.asset_id).judgment=body.judgment;
      return route.fulfill({json:query});
    }
    throw Error('Unexpected request '+req.method()+' '+path);
  });
  const active=()=>page.locator('[data-match-active="true"]'),blur=()=>page.evaluate(()=>document.activeElement?.blur());
  const waitActive=async id=>{await page.waitForFunction(id=>document.querySelector('[data-match-active="true"]')?.id==='match-'+id,id);};
  try{
    await page.setViewportSize({width:1440,height:1000});await page.goto(origin+'/annotate/project/vector-project?mode=text&query_id=query-1');
    await page.getByText('当前查询：穿黑色衣服的人 · 模型 r1 · 3 个结果',{exact:true}).waitFor();await blur();await page.keyboard.press('ArrowRight');await waitActive('hit-1');
    await page.keyboard.press('1');await page.keyboard.press('2');await page.waitForFunction(()=>!document.querySelector('.ant-btn-loading'));
    await active().getByRole('radio',{name:'正确',exact:true}).check();
    if(writes.length!==1||writes[0].asset_id!=='hit-1'||writes[0].judgment!=='correct')throw Error('Judgment shortcut did not target one active result');
    await blur();await page.keyboard.press('ArrowRight');await waitActive('hit-2');await page.keyboard.press('2');
    await active().getByRole('radio',{name:'错误',exact:true}).check();
    await blur();await page.keyboard.press('3');await active().getByRole('radio',{name:'无法判断',exact:true}).check();
    await blur();const count=writes.length;await page.keyboard.press('3');await page.keyboard.press('ArrowRight');await waitActive('hit-2');if(writes.length!==count)throw Error('Duplicate judgment wrote data');
    checks.push('Arrows select one result, 1/2/3 save correct/incorrect/unknown with current version; busy and duplicate actions are blocked');
    await page.getByLabel('匹配查询文本',{exact:true}).fill('123');await page.getByLabel('匹配查询文本',{exact:true}).press('1');await page.getByLabel('匹配查询文本',{exact:true}).press('ArrowLeft');
    if(writes.length!==count||await active().getAttribute('id')!=='match-hit-2')throw Error('Text editing triggered a shortcut');
    await blur();await page.getByRole('button',{name:'快捷键',exact:true}).click();await page.getByRole('dialog',{name:'向量标注快捷键',exact:true}).waitFor();
    await page.keyboard.press('1');if(writes.length!==count)throw Error('Help modal allowed a judgment');await page.keyboard.press('Escape');
    await page.screenshot({path:'output/playwright/vector-shortcuts.png',animations:'disabled'});
    checks.push('Text input and shortcut-help modal do not trigger judgments');
    await page.goto(origin+'/annotate/project/vector-project?mode=image&query_id=query-1');await waitActive('hit-0');await blur();await page.keyboard.press('ArrowRight');await waitActive('hit-1');
    checks.push('Image-query mode uses the same keyboard navigation');
    return {checks,fixtureWrites:writes.length,businessWrites:0};
  }finally{await page.goto('about:blank');await page.unrouteAll({behavior:'ignoreErrors'});}
}
