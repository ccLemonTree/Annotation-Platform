async page=>{
  await page.unrouteAll();page.setDefaultTimeout(10000);await page.setViewportSize({width:1440,height:1000});
  const origin='http://127.0.0.1:3000',checks=[],requests=[],errors=[];
  await page.addInitScript(()=>{if(location.origin==='http://127.0.0.1:3000')localStorage.setItem('user',JSON.stringify({id:1,tenant_id:1,role:'admin'}));});
  const src=await(await page.request.get(origin+'/src/pages/DetectionWorkspace.tsx')).text();
  const react=src.match(/["'](\/node_modules\/\.vite\/deps\/react\.js\?[^"']+)["']/)[1],router=src.match(/["'](\/node_modules\/\.vite\/deps\/react-router-dom\.js\?[^"']+)["']/)[1];
  const project={id:'11111111-1111-4111-8111-111111111111',project_name:'审核筛选测试',task_type:'detection',label_schema:{labels:['bike','ele','person']}};
  const assets=Array.from({length:6},(_,i)=>({id:'aaaaaaaa-0000-4000-8000-'+String(i).padStart(12,'0'),project_id:project.id,original_name:'fixture-'+i+'.jpg',width:640,height:480,source_type:'upload'}));
  const states=['pending','approved','pending','rejected','approved','pending'];
  const records=assets.map((a,i)=>({id:'annotation-'+i,asset_id:a.id,version:1,is_latest:true,review_status:states[i],annotation_data:{boxes:i===4?[]:[{label:i===2?'ele':'bike',x:10,y:20,width:50,height:60}]}}));
  let capability=true,reviews=0;
  const recordError=error=>errors.push(error.message);page.on('pageerror',recordError);
  await page.route('**/src/App.tsx*',route=>route.fulfill({contentType:'application/javascript',body:`import React from '${react}';import{createBrowserRouter,RouterProvider}from'${router}';import Provider from '/src/ui/PlatformProvider.tsx';import Layout from '/src/layouts/MainLayout.tsx';import Workspace from '/src/pages/DetectionWorkspace.tsx';import{CurrentUser}from'/src/components/currentUser.ts';const router=createBrowserRouter([{element:React.createElement(CurrentUser.Provider,{value:{id:1,tenant_id:1,username:'fixture',role:'admin'}},React.createElement(Provider,null,React.createElement(Layout))),children:[{path:'/annotate/:assetId',element:React.createElement(Workspace)}]}]);export default function App(){return React.createElement(RouterProvider,{router});}`}));
  await page.route('**/api/**',async route=>{
    const req=route.request(),url=new URL(req.url()),path=url.pathname;
    if(!path.startsWith('/api/'))return route.continue();
    if(path.startsWith('/api/analytics/'))return route.fulfill({json:{}});
    if(path==='/api/projects/'+project.id)return route.fulfill({json:project});
    if(path==='/api/model-services')return route.fulfill({json:[]});
    const review=url.searchParams.get('review_status'),label=url.searchParams.get('label');
    const matching=assets.filter((a,i)=>(!review||records[i].review_status===review)&&(!label||records[i].annotation_data.boxes.some(box=>box.label===label)));
    const headers={'X-Annotation-Label-Filter':'1',...(capability?{'X-Annotation-Review-Filter':'1'}:{})};
    if(path==='/api/assets'){
      requests.push(Object.fromEntries(url.searchParams));const size=Number(url.searchParams.get('page_size')||20),offset=(Number(url.searchParams.get('page')||1)-1)*size;
      return route.fulfill({headers,json:{items:matching.slice(offset,offset+size),total:matching.length}});
    }
    if(path.startsWith('/api/assets/')){
      const asset=assets.find(a=>a.id===path.split('/')[3]);if(!asset)throw Error('Unknown fixture asset');
      if(path.endsWith('/content'))return route.fulfill({contentType:'image/png',body:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jUKcAAAAASUVORK5CYII=','base64')});
      if(path.endsWith('/neighbors')){
        const i=matching.indexOf(asset),anchor=assets.indexOf(asset);
        return route.fulfill({headers,json:{position:i+1,total:matching.length,previous_id:i>=0?matching[i-1]?.id||null:matching.filter(a=>assets.indexOf(a)<anchor).at(-1)?.id||null,
          next_id:i>=0?matching[i+1]?.id||null:matching.find(a=>assets.indexOf(a)>anchor)?.id||null,...(review?{matched:i>=0}:{})}});
      }
      return route.fulfill({json:asset});
    }
    if(path.endsWith('/events'))return route.fulfill({json:[]});
    if(path==='/api/annotations')return route.fulfill({json:{items:[records.find(row=>row.asset_id===url.searchParams.get('asset_id'))],total:1}});
    if(path.endsWith('/review')){reviews++;const row=records.find(row=>path.includes(row.id));row.review_status=req.postDataJSON().approved?'approved':'rejected';return route.fulfill({json:row});}
    throw Error('Unexpected fixture request '+req.method()+' '+path);
  });
  const canvas=page.getByRole('img',{name:'检测画框区域'});
  const select=async(name,value)=>{await page.getByRole('combobox',{name,exact:true}).click();await page.getByText(value,{exact:true}).last().click();};
  const current=async index=>{await page.waitForURL(url=>url.pathname.endsWith(assets[index].id));await page.getByText(`${project.project_name} · ${assets[index].original_name}`,{exact:true}).waitFor();await canvas.waitFor();};
  try{
    await page.goto(origin+'/annotate/'+assets[0].id+'?from=review&project_id='+project.id+'&review_status=pending&page=4');await current(0);await page.getByText('/ 3',{exact:true}).waitFor();
    checks.push('review_status inherited from review entry scopes the queue to pending annotations');
    await page.getByRole('button',{name:'下一张',exact:true}).click();await current(2);await page.getByRole('button',{name:'上一张',exact:true}).click();await current(0);
    await page.getByRole('spinbutton',{name:'跳转图片序号'}).fill('3');await page.getByRole('spinbutton',{name:'跳转图片序号'}).press('Enter');await current(5);
    if(!requests.some(row=>row.review_status==='pending'&&row.page==='3'))throw Error('Ordinal request omitted review filter');
    await page.reload();await current(5);await page.getByText('/ 3',{exact:true}).waitFor();checks.push('previous, next, ordinal jump and reload retain review scope');
    await select('筛选标注标签','bike');await current(0);await page.getByText('/ 2',{exact:true}).waitFor();
    if(new URL(page.url()).searchParams.get('page'))throw Error('Changed scope retained stale review-list page');
    checks.push('label and review filters intersect and changing scope resets the list-page context');
    await page.getByRole('button',{name:'审核通过',exact:true}).click();await page.getByText('当前图片已不符合筛选，上一张 / 下一张仍在筛选结果中切换',{exact:true}).waitFor();
    if(!page.url().includes(assets[0].id))throw Error('Review unexpectedly auto-navigated');await page.getByText('/ 1',{exact:true}).waitFor();
    await page.getByRole('button',{name:'下一张',exact:true}).click();await current(5);await page.getByRole('button',{name:'审核通过',exact:true}).click();
    await page.getByText('当前筛选已无匹配图片，可清除筛选查看全部',{exact:true}).waitFor();
    if(await page.getByRole('button',{name:'下一张',exact:true}).isEnabled())throw Error('Empty filtered queue enables next');
    checks.push('review refreshes the scoped queue, keeps the image visible and handles an empty result');
    await select('筛选审核状态','已通过');await current(0);await page.getByText('/ 3',{exact:true}).waitFor();
    const before=page.url();await select('筛选标注标签','ele');await page.getByText('当前审核状态与标签筛选没有匹配图片',{exact:true}).waitFor();
    if(page.url()!==before)throw Error('Empty filter changed the current queue');checks.push('empty filter selection preserves image and previous scope');
    const back=await page.getByRole('link',{name:/返回 .*标注与审核/}).getAttribute('href');if(!back.includes('review_status=approved'))throw Error('Review return link lost status');
    await select('筛选审核状态','全部状态');await page.getByText('/ 4',{exact:true}).waitFor();if(new URL(page.url()).searchParams.has('review_status'))throw Error('All states did not clear review query');
    await select('筛选审核状态','已驳回');await current(3);await page.getByText('/ 1',{exact:true}).waitFor();checks.push('approved, rejected and all states select correctly; return link retains status');
    await canvas.focus();await page.keyboard.press('n');await page.getByText('未保存',{exact:true}).waitFor();if(await page.getByRole('combobox',{name:'筛选审核状态'}).isEnabled())throw Error('Dirty draft allows scope changes');await page.keyboard.press('Control+z');
    checks.push('unsaved edits block review-filter changes');
    await page.screenshot({path:'output/playwright/workspace-review-filter-desktop.png'});
    await page.setViewportSize({width:390,height:844});if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1))throw Error('Mobile workspace overflows horizontally');
    await page.screenshot({path:'output/playwright/workspace-review-filter-mobile.png',fullPage:true});
    capability=false;await page.goto(origin+'/annotate/'+assets[2].id+'?review_status=pending');await canvas.waitFor();
    await page.getByText('审核筛选队列读取失败，请重试并确认后台支持审核状态筛选',{exact:true}).waitFor();if(await page.getByRole('button',{name:'下一张',exact:true}).isEnabled())throw Error('Ignored filter displayed an unfiltered queue');
    checks.push('missing backend support fails closed instead of showing unfiltered navigation');
    if(errors.length)throw Error(errors.join('; '));return {passed:true,checks,mocked_reviews:reviews,business_writes:0};
  }catch(error){await page.screenshot({path:'output/playwright/workspace-review-filter-failure.png'});throw error;}
  finally{await page.goto('about:blank');await page.unrouteAll();page.off('pageerror',recordError);}
}

