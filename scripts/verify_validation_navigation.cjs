async page=>{
  await page.unrouteAll();page.setDefaultTimeout(8000);
  const origin='http://127.0.0.1:3000',id='aaaaaaaa-0000-4000-8000-000000000003',checks=[],calls=[];
  const source=await(await page.request.get(origin+'/src/components/validation/ValidationResults.tsx')).text();
  const react=source.match(/["'](\/node_modules\/\.vite\/deps\/react\.js\?[^"']+)["']/)[1];
  const app='**/src/App.tsx*',api='**/api/**';
  const metrics={images:21,tp:0,fp:0,fn:21,per_class:[],coverage:{total:21,succeeded:21,complete:true}};
  const row=n=>({id:'item-'+n,original_name:'missing-fire-'+n+'.jpg',width:640,height:480,status:'completed',truth:[{label:'fire',x:10,y:10,width:20,height:20}],predictions:[],evaluation:{tp:0,fp:0,fn:1,unmatched_truth:[0],unmatched_predictions:[]}});
  await page.route(app,route=>route.fulfill({contentType:'application/javascript',body:`import React from '${react}';import Provider from '/src/ui/PlatformProvider.tsx';import Results from '/src/components/validation/ValidationResults.tsx';export default function App(){return React.createElement(Provider,null,React.createElement(Results,{runId:'${id}',revision:0,onClose:()=>{}}));}`}));
  await page.route(api,async route=>{
    const req=route.request(),url=new URL(req.url()),path=url.pathname;
    if(!path.startsWith('/api/'))return route.continue();
    if(req.method()!=='GET')throw Error('Unexpected write '+path);
    calls.push({path,query:Object.fromEntries(url.searchParams)});
    if(path.endsWith('/image'))return route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="640" height="480"/>'});
    if(path.endsWith('/items')){
      const current=Number(url.searchParams.get('page'))||1;
      await new Promise(resolve=>setTimeout(resolve,200));
      return route.fulfill({json:{items:current===1?Array.from({length:20},(_,i)=>row(i+1)):[row(21)],total:21,page:current,page_size:20}});
    }
    if(path.endsWith('/'+id))return route.fulfill({json:{id,name:'Navigation fixture',status:'completed',processed:21,total:21,labels:['fire','smoke'],config:{confidence:.25},model:{name:'Detector'},metrics,target_metrics:metrics}});
    throw Error('Unexpected read '+path);
  });
  const choose=async(name,text)=>{await page.getByRole('combobox',{name,exact:true}).click();await page.locator('.ant-select-dropdown:visible').getByText(text,{exact:true}).last().click();};
  const dialog=()=>page.getByRole('dialog');
  const expectImage=async n=>{await dialog().getByText('missing-fire-'+n+'.jpg · 已完成',{exact:true}).waitFor();};
  try{
    await page.setViewportSize({width:1440,height:1000});await page.goto(origin+'/validation');
    await page.getByRole('button',{name:'查看叠框'}).first().waitFor();
    await choose('错误类型','漏检');await choose('错误标签','fire');
    await page.getByRole('button',{name:'查看叠框'}).first().click();await expectImage(1);
    await page.keyboard.press('ArrowLeft');await expectImage(1);await page.keyboard.press('ArrowRight');await expectImage(2);await page.keyboard.press('ArrowLeft');await expectImage(1);
    checks.push('Validation modal left/right select adjacent filtered samples; first boundary stays put');
    await page.getByRole('button',{name:'关闭预览',exact:true}).click();
    await page.getByRole('button',{name:'查看叠框'}).nth(19).click();await expectImage(20);
    await page.keyboard.press('ArrowRight');await page.keyboard.press('ArrowRight');await expectImage(21);
    await page.keyboard.press('ArrowRight');await expectImage(21);await page.keyboard.press('ArrowLeft');await expectImage(20);
    if(!calls.some(c=>c.path.endsWith('/items')&&c.query.page==='2'&&c.query.error_type==='false_negative'&&c.query.label==='fire'&&c.query.mode==='target'&&c.query.target_overlap===undefined))throw Error('Cross-page request lost filters or task policy');
    checks.push('Cross-page navigation preserves error type, label, overlap threshold; last boundary stays put');
    await page.getByRole('button',{name:'上一张图片',exact:true}).click();await expectImage(19);
    await page.getByRole('button',{name:'下一张图片',exact:true}).click();await expectImage(20);
    await page.keyboard.press('Escape');await dialog().waitFor({state:'hidden'});
    const before=calls.length;await page.keyboard.press('ArrowRight');await page.waitForTimeout(250);
    if(calls.length!==before)throw Error('Closed modal still navigates');
    checks.push('Navigation buttons work and closed modal releases keyboard');
    await page.getByRole('button',{name:'查看叠框'}).first().click();await expectImage(1);
    await page.screenshot({path:'output/playwright/validation-navigation.png',animations:'disabled'});
    await page.setViewportSize({width:390,height:844});await page.screenshot({path:'output/playwright/validation-navigation-mobile.png',animations:'disabled'});
    return {checks,businessWrites:0};
  }finally{await page.goto('about:blank');await page.unroute(api);await page.unroute(app);}
}
