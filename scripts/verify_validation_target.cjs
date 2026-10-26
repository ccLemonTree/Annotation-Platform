async page=>{
  page.setDefaultTimeout(8000);const origin='http://127.0.0.1:3000',calls=[],checks=[];
  const source=await(await page.request.get(origin+'/src/components/validation/ValidationResults.tsx')).text();
  const react=source.match(/["'](\/node_modules\/\.vite\/deps\/react\.js\?[^"']+)["']/)[1];
  const runId='aaaaaaaa-0000-4000-8000-000000000001',itemId='bbbbbbbb-0000-4000-8000-000000000001';
  const truth=[{label:'smoke',x:50,y:50,width:100,height:100}],predictions=[{...truth[0],x:130,score:.9},{...truth[0],x:130,score:.8},{...truth[0],x:240,y:240,score:.2}];
  const standard={tp:0,fp:2,fn:1,precision:0,recall:0,f1:0,map50:0,map50_95:0,images:1,negative_images:0,false_positive_negative_images:0,per_class:[],confusion_matrix:{labels:['smoke','背景'],matrix:[[0,1],[2,0]]},coverage:{total:1,succeeded:1,failed:0,pending:0,canceled:0,complete:true}};
  const target={...standard,tp:1,fp:0,fn:0,precision:1,recall:1,f1:1,merged_predictions:1};
  const app='**/src/App.tsx*',api='**/api/**';
  await page.route(app,route=>route.fulfill({contentType:'application/javascript',body:`import React from '${react}';import Provider from '/src/ui/PlatformProvider.tsx';import Results from '/src/components/validation/ValidationResults.tsx';export default function App(){return React.createElement(Provider,null,React.createElement(Results,{runId:'${runId}',revision:0,onClose:()=>{}}));}`}));
  await page.route(api,route=>{
    const req=route.request(),url=new URL(req.url()),path=url.pathname;
    if(!path.startsWith('/api/'))return route.continue();
    if(req.method()!=='GET')throw Error('Unexpected write '+path);
    calls.push({path,query:Object.fromEntries(url.searchParams)});
    const tolerant=url.searchParams.get('mode')==='target'&&Number(url.searchParams.get('target_overlap'))<=.2;
    if(path.endsWith('/image'))return route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="640" height="480"><rect width="640" height="480" fill="#333"/></svg>'});
    if(path.endsWith('/items')){
      const error=url.searchParams.get('error_type'),label=url.searchParams.get('label');
      const empty=label==='fire'||error==='correct'&&!tolerant||(error==='false_negative'||error==='false_positive')&&tolerant;
      return route.fulfill({json:{items:empty?[]:[{id:itemId,asset_id:itemId,original_name:'smoke.jpg',width:640,height:480,status:'completed',truth,predictions,evaluation:tolerant?{tp:1,fp:0,fn:0,ignored_duplicates:[1],unmatched_truth:[],unmatched_predictions:[]}:{tp:0,fp:2,fn:1,unmatched_truth:[0],unmatched_predictions:[0,1]},error:null}],total:empty?0:1,page:1,page_size:20}});
    }
    if(path.endsWith('/report'))return route.fulfill({json:{fixture:true}});
    if(path.endsWith('/'+runId))return route.fulfill({json:{id:runId,name:'烟火容差验证',status:'completed',processed:1,total:1,failed_count:0,labels:['smoke','fire'],updated_at:'2026-10-20T07:00:00Z',config:{inference_confidence:.2,confidence:.25,match_iou:.5,nms_iou:.45},model:{name:'RF-DETR',revision:1},metrics:standard,target_metrics:tolerant?target:standard}});
    throw Error('Unexpected read '+path);
  });
  const choose=async(name,text)=>{await page.getByRole('combobox',{name,exact:true}).click();await page.locator('.ant-select-dropdown:visible').getByText(text,{exact:true}).last().click();};
  try{
    await page.setViewportSize({width:1440,height:1000});await page.goto(origin+'/validation');
    await page.getByTestId('metric-tp').getByText('1',{exact:true}).waitFor();
    await page.getByRole('button',{name:'查看叠框'}).click();await page.getByRole('img',{name:'真值与预测叠框'}).waitFor();
    if(await page.getByRole('img',{name:'真值与预测叠框'}).locator('rect').count()!==3)throw Error('Merged duplicate remains visible');
    await page.getByRole('img',{name:'真值与预测叠框'}).getByText('smoke 0.200（低分）',{exact:true}).waitFor();
    await page.getByRole('dialog').getByText('当前判定：TP 1 · FP 0 · FN 0；已合并重复框 1 个。',{exact:true}).waitFor();
    await page.screenshot({path:'output/playwright/validation-target-overlay.png'});
    await page.getByRole('button',{name:'关闭预览',exact:true}).click();
    checks.push('Default target detection merges duplicate counts and overlay while preserving raw predictions');
    await choose('判定方式','标准框匹配（原任务参数）');await page.getByTestId('metric-tp').getByText('0',{exact:true}).waitFor();
    await choose('判定方式','目标检出（合并重复框）');await page.getByTestId('metric-tp').getByText('1',{exact:true}).waitFor();
    await page.getByRole('spinbutton',{name:'交集阈值（%）'}).fill('50');await page.getByRole('spinbutton',{name:'交集阈值（%）'}).press('Tab');
    await page.getByTestId('metric-tp').getByText('0',{exact:true}).waitFor();
    await page.getByRole('spinbutton',{name:'交集阈值（%）'}).fill('20');await page.getByRole('spinbutton',{name:'交集阈值（%）'}).press('Tab');
    await page.getByTestId('metric-tp').getByText('1',{exact:true}).waitFor();
    await choose('错误类型','正确');await page.getByText('smoke.jpg',{exact:true}).waitFor();
    await page.getByRole('button',{name:'下载 JSON 报告',exact:true}).click();
    await page.waitForFunction(()=>!document.querySelector('.ant-btn-loading'));
    if(!calls.some(c=>c.path.endsWith('/report')&&c.query.mode==='target'&&Number(c.query.target_overlap)===.2))throw Error('Report omitted target policy');
    checks.push('Mode, tolerance, error filtering and JSON download share the selected policy');
    await choose('错误类型','漏检');await choose('错误标签','smoke');
    await page.getByText('暂无符合条件的样本',{exact:true}).waitFor();
    await choose('判定方式','标准框匹配（原任务参数）');
    await choose('错误类型','漏检');await choose('错误标签','smoke');await page.getByText('smoke.jpg',{exact:true}).waitFor();
    await page.getByRole('button',{name:'查看叠框'}).click();
    await page.getByRole('dialog').getByText('漏检标签：smoke ×1；误报标签：smoke ×2。',{exact:true}).waitFor();
    await page.getByRole('img',{name:'真值与预测叠框'}).getByText('smoke（漏检）',{exact:true}).waitFor();
    await page.screenshot({path:'output/playwright/validation-label-missing.png',animations:'disabled'});
    await page.getByRole('button',{name:'关闭预览',exact:true}).click();await choose('错误标签','fire');
    await page.getByText('暂无符合条件的样本',{exact:true}).waitFor();
    if(!calls.some(c=>c.path.endsWith('/items')&&c.query.error_type==='false_negative'&&c.query.label==='fire'&&c.query.page==='1'&&c.query.mode==='standard'))throw Error('Label filter omitted current error or mode');
    const labelSelect=page.locator('.ant-select').filter({has:page.getByRole('combobox',{name:'错误标签',exact:true})});
    await labelSelect.hover();await labelSelect.locator('.ant-select-clear').click();await page.getByText('smoke.jpg',{exact:true}).waitFor();
    checks.push('Label and error filters combine, clear correctly and highlight the actual missed class');
    await page.setViewportSize({width:390,height:844});
    await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
    await page.waitForFunction(()=>document.documentElement.scrollWidth<=innerWidth+1);
    await page.screenshot({path:'output/playwright/validation-target-mobile.png',fullPage:true});
    if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1))throw Error('Mobile page overflow '+JSON.stringify(await page.locator('body').evaluate(el=>[...el.children].map(c=>({class:c.className,width:c.getBoundingClientRect().width,scroll:c.scrollWidth})))));
    checks.push('390px report remains within viewport');
    return {checks,businessWrites:0};
  }finally{await page.goto('about:blank');await page.unroute(api);await page.unroute(app);}
}
