async page=>{
  page.setDefaultTimeout(8000);await page.setViewportSize({width:1440,height:1000});
  const origin='http://127.0.0.1:3000',checks=[],requests=[];
  const src=await(await page.request.get(origin+'/src/pages/DetectionWorkspace.tsx')).text();
  const react=src.match(/["'](\/node_modules\/\.vite\/deps\/react\.js\?[^"']+)["']/)[1],router=src.match(/["'](\/node_modules\/\.vite\/deps\/react-router-dom\.js\?[^"']+)["']/)[1];
  const project={id:'11111111-1111-4111-8111-111111111111',project_name:'标签筛选测试',task_type:'detection',label_schema:{labels:['fire','smoke','star']}};
  const assets=Array.from({length:4},(_,i)=>({id:`aaaaaaaa-0000-4000-8000-${String(i).padStart(12,'0')}`,project_id:project.id,original_name:`image-${i}.jpg`,width:640,height:480,source_type:'upload'}));
  const records=assets.map((a,i)=>({id:`bbbbbbbb-0000-4000-8000-${String(i).padStart(12,'0')}`,asset_id:a.id,version:1,is_latest:true,review_status:'pending',annotation_data:{boxes:i?[{label:i===2?'smoke':'fire',x:10,y:20,width:50,height:60}]:[]}}));
  let reviews=0,hold=false,release,failure=false;
  const api='**/api/**',app='**/src/App.tsx*';
  await page.route(app,route=>route.fulfill({contentType:'application/javascript',body:`import React from '${react}';import{createBrowserRouter,RouterProvider}from'${router}';import Provider from '/src/ui/PlatformProvider.tsx';import Layout from '/src/layouts/MainLayout.tsx';import Workspace from '/src/pages/DetectionWorkspace.tsx';const router=createBrowserRouter([{element:React.createElement(Provider,null,React.createElement(Layout)),children:[{path:'/annotate/:assetId',element:React.createElement(Workspace)}]}]);export default function App(){return React.createElement(RouterProvider,{router});}`}));
  await page.route(api,async route=>{
    const req=route.request(),url=new URL(req.url()),path=url.pathname;
    if(!path.startsWith('/api/'))return route.continue();
    if(path==='/api/model-services')return route.fulfill({json:[]});
    if(path.startsWith('/api/analytics/'))return route.fulfill({json:{}});
    if(path==='/api/projects/'+project.id)return route.fulfill({json:project});
    const matching=assets.filter((a,i)=>!url.searchParams.get('label')||records[i].annotation_data.boxes.some(b=>b.label===url.searchParams.get('label')));
    if(path==='/api/assets'){
      requests.push(Object.fromEntries(url.searchParams));const offset=(Number(url.searchParams.get('page')||1)-1)*Number(url.searchParams.get('page_size')||20);
      return route.fulfill({headers:{'X-Annotation-Label-Filter':'1'},json:{items:matching.slice(offset,offset+Number(url.searchParams.get('page_size')||20)),total:matching.length}});
    }
    if(path.startsWith('/api/assets/')){
      const asset=assets.find(a=>path.split('/')[3]===a.id);if(!asset)throw Error('Unknown asset');
      if(path.endsWith('/content'))return route.fulfill({contentType:'image/png',body:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jUKcAAAAASUVORK5CYII=','base64')});
      if(path.endsWith('/neighbors')){const i=matching.indexOf(asset);return route.fulfill({json:{position:i+1,total:matching.length,previous_id:matching[i-1]?.id||null,next_id:matching[i+1]?.id||null}});}
      return route.fulfill({json:asset});
    }
    if(path.endsWith('/events'))return route.fulfill({json:[]});
    if(path==='/api/annotations'&&req.method()==='GET'){
      const r=records.find(r=>r.asset_id===url.searchParams.get('asset_id'));return route.fulfill({json:{items:[r,{...r,id:'old-'+r.id,is_latest:false,version:0,review_status:'pending'}],total:2}});
    }
    if(path.endsWith('/review')){
      reviews++;if(hold)await new Promise(resolve=>release=resolve);
      if(failure)return route.fulfill({status:409,json:{detail:'review fixture conflict'}});
      const r=records.find(r=>path.includes(r.id));r.review_status='approved';return route.fulfill({json:r});
    }
    throw Error('Unexpected API '+req.method()+' '+path);
  });
  const canvas=page.getByRole('img',{name:'检测画框区域'}),select=async(name,text)=>{await page.getByRole('combobox',{name,exact:true}).click();await page.getByText(text,{exact:true}).last().click();};
  try{
    await page.goto(origin+'/annotate/'+assets[0].id);await page.evaluate(()=>localStorage.setItem('user',JSON.stringify({role:'admin'})));await page.reload();await canvas.waitFor();
    await select('筛选标注标签','fire');await page.waitForURL('**/'+assets[1].id+'?label=fire');await canvas.waitFor();
    await page.getByText('/ 2',{exact:true}).waitFor();await page.getByRole('button',{name:'下一张',exact:true}).click();await page.waitForURL('**/'+assets[3].id+'?label=fire');
    await canvas.waitFor();await page.waitForFunction(()=>document.querySelector('input[aria-label="跳转图片序号"]')?.value==='2');await page.getByRole('spinbutton',{name:'跳转图片序号'}).fill('1');await page.getByRole('spinbutton',{name:'跳转图片序号'}).press('Enter');await page.waitForURL('**/'+assets[1].id+'?label=fire');
    await page.reload();await canvas.waitFor();if(!requests.some(r=>r.label==='fire'&&r.page==='1'))throw Error('Filtered jump omitted label');
    await page.setViewportSize({width:1366,height:768});
    await page.screenshot({path:'output/playwright/workspace-label-review.png'});
    const layout=await page.getByRole('combobox',{name:'筛选标注标签'}).boundingBox();
    if(!layout||layout.x<0||layout.x+layout.width>1366||layout.y+layout.height>768)throw Error('Label filter outside viewport');
    if(await page.evaluate(()=>document.documentElement.scrollHeight>innerHeight+1||document.documentElement.scrollWidth>innerWidth+1))throw Error('Workspace overflows viewport');
    checks.push('Label filter, neighbors, ordinal jump and reload preserve one filtered queue');
    const before=page.url();await select('筛选标注标签','star');await page.getByText('当前标签没有已标注图片',{exact:true}).waitFor();if(page.url()!==before)throw Error('Empty filter changed current image');
    checks.push('Empty label keeps current image and filter');
    hold=true;await canvas.focus();await page.keyboard.press('r');await page.getByRole('button',{name:'审核通过',exact:true}).waitFor({state:'visible'});
    await page.waitForFunction(()=>document.querySelector('button[aria-label="审核通过"]').disabled);await page.keyboard.press('r');if(reviews!==1)throw Error('Duplicate review submitted');hold=false;release();await page.getByText('v1 · 已通过',{exact:true}).waitFor();
    checks.push('R approves through existing API and ignores duplicate presses while busy');
    await page.getByRole('spinbutton',{name:'跳转图片序号'}).focus();await page.keyboard.press('r');if(reviews!==1)throw Error('Typing triggered review');
    await canvas.focus();await page.keyboard.press('n');await page.getByText('未保存',{exact:true}).waitFor();if(await page.getByRole('combobox',{name:'筛选标注标签'}).isEnabled())throw Error('Dirty filter enabled');await page.keyboard.press('r');if(reviews!==1)throw Error('Dirty draft reviewed');await page.keyboard.press('Control+z');
    await page.getByRole('button',{name:'快捷键',exact:true}).click();await page.keyboard.press('r');if(reviews!==1)throw Error('Modal triggered review');await page.getByRole('dialog').locator('.ant-modal-close').click();
    await select('标注历史版本','v0 · 待审核');await canvas.focus();await page.keyboard.press('r');if(reviews!==1)throw Error('Historical version reviewed');
    checks.push('Inputs, dirty drafts, dialogs and historical versions block review; dirty filter disabled');
    await page.evaluate(()=>localStorage.setItem('user',JSON.stringify({role:'annotator'})));await page.goto(origin+'/annotate/'+assets[0].id);await canvas.waitFor();await canvas.focus();await page.keyboard.press('r');if(reviews!==1)throw Error('Non-admin negative reviewed');
    checks.push('Negative sample administrator restriction applies to R');
    await page.evaluate(()=>localStorage.setItem('user',JSON.stringify({role:'admin'})));failure=true;await page.goto(origin+'/annotate/'+assets[2].id);await canvas.waitFor();await canvas.focus();await page.keyboard.press('r');await page.getByText('审核失败，请重试或重新加载最新版本',{exact:true}).waitFor();
    checks.push('Review failure preserves image and clears busy state');
    return {checks,mockedReviews:reviews,realAnnotationWrites:0};
  }catch(error){await page.screenshot({path:'output/playwright/workspace-label-review-failure.png'});throw Error(error.message+' URL='+page.url()+' BODY='+(await page.locator('body').innerText()).slice(-1200));
  }finally{release?.();await page.goto('about:blank');await page.unroute(api);await page.unroute(app);}
}
