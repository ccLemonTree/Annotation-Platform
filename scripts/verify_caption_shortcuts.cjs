async page=>{
  await page.unrouteAll();page.setDefaultTimeout(8000);
  let completed=false;
  const origin='http://127.0.0.1:3000',checks=[],writes=[];
  const source=await(await page.request.get(origin+'/src/pages/AnnotationWorkspace.tsx')).text();
  const react=source.match(/["'](\/node_modules\/\.vite\/deps\/react\.js\?[^"']+)["']/)[1],router=source.match(/["'](\/node_modules\/\.vite\/deps\/react-router-dom\.js\?[^"']+)["']/)[1];
  const project={id:'aaaaaaaa-0000-4000-8000-000000000001',project_name:'描述快捷键测试',task_type:'caption',label_schema:{labels:['fire','smoke']}};
  const assets=Array.from({length:3},(_,i)=>({id:'asset-'+i,project_id:project.id,original_name:'caption-'+i+'.jpg',width:640,height:480,source_type:'upload'}));
  const records=assets.map((asset,i)=>({id:'annotation-'+i,asset_id:asset.id,version:1,is_latest:true,review_status:'pending',annotation_data:{description:'原描述 '+i,labels:['fire']},created_at:'2026-10-22T00:00:00Z'}));
  let failSave=false;
  const app='**/src/App.tsx*',api='**/api/**';
  await page.route(app,route=>route.fulfill({contentType:'application/javascript',body:`import React from '${react}';import{createBrowserRouter,RouterProvider}from'${router}';import Provider from '/src/ui/PlatformProvider.tsx';import Workspace from '/src/pages/AnnotationWorkspace.tsx';const router=createBrowserRouter([{path:'/annotate/:assetId',element:React.createElement(Provider,null,React.createElement(Workspace))}]);export default function App(){return React.createElement(RouterProvider,{router});}`}));
  await page.route(api,async route=>{
    const req=route.request(),url=new URL(req.url()),path=url.pathname;
    if(!path.startsWith('/api/'))return route.continue();
    if(path==='/api/projects/'+project.id)return route.fulfill({json:project});
    if(path.startsWith('/api/assets/')){
      const asset=assets.find(a=>path.split('/')[3]===a.id);if(!asset)throw Error('Unknown asset');
      if(path.endsWith('/content'))return route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="640" height="480"/>'});
      if(path.endsWith('/neighbors')){const index=assets.indexOf(asset);return route.fulfill({json:{previous_id:assets[index-1]?.id||null,next_id:assets[index+1]?.id||null,position:index+1,total:3}});}
      return route.fulfill({json:asset});
    }
    if(path==='/api/annotations'&&req.method()==='GET')return route.fulfill({json:{items:records.filter(r=>r.asset_id===url.searchParams.get('asset_id')),total:1}});
    if(path.startsWith('/api/annotations/')&&['POST','PUT'].includes(req.method())){
      const body=req.postDataJSON();writes.push({path,body});
      const record=records.find(r=>path.includes(r.id));if(!record)throw Error('Unknown annotation');
      if(path.endsWith('/review'))record.review_status=body.approved?'approved':'rejected';
      else{
        await new Promise(resolve=>setTimeout(resolve,150));
        if(failSave)return route.fulfill({status:409,json:{detail:'保存冲突测试'}});
        record.annotation_data=body.annotation_data;record.version++;record.id=record.id+'v';record.review_status='pending';
      }
      return route.fulfill({json:record});
    }
    throw Error('Unexpected request '+req.method()+' '+path);
  });
  const blur=()=>page.evaluate(()=>document.activeElement?.blur()),description=()=>page.getByLabel('图片描述',{exact:true});
  const waitAsset=index=>page.getByText(`caption-${index}.jpg · 640 × 480`,{exact:true}).waitFor();
  try{
    await page.setViewportSize({width:1440,height:1000});await page.goto(origin+'/annotate/asset-0?from=review&review_status=pending');
    await waitAsset(0);await blur();await page.keyboard.press('ArrowRight');await page.waitForURL('**/asset-1?from=review&review_status=pending');
    await waitAsset(1);await blur();await page.keyboard.press('a');await page.waitForURL('**/asset-0?from=review&review_status=pending');
    await waitAsset(0);await blur();await page.keyboard.press('ArrowLeft');if(!page.url().includes('/asset-0?'))throw Error('First image wrapped');
    checks.push('Caption arrows and A/D navigate with query context and respect first boundary');
    await description().fill('新描述');await description().press('ArrowRight');await description().press('r');
    if(writes.length||!page.url().includes('/asset-0?'))throw Error('Typing triggered a global shortcut');
    await description().press('Control+s');await page.getByText('有未保存修改',{exact:true}).waitFor({state:'hidden'});
    if(writes.length!==1||writes[0].body.base_version!==1)throw Error('Ctrl+S did not save once from textarea');
    await blur();await page.keyboard.press('r');await page.getByText('v2 · 已通过',{exact:true}).waitFor();
    const approvedWrites=writes.length;await page.keyboard.press('r');if(writes.length!==approvedWrites)throw Error('Repeated approval sent a duplicate');
    await page.getByLabel('审核意见',{exact:true}).fill('需复核');await blur();await page.keyboard.press('Shift+r');await page.getByText('v2 · 已驳回',{exact:true}).waitFor();
    if(writes.at(-1).body.approved!==false||writes.at(-1).body.review_comment!=='需复核')throw Error('Reject did not preserve review comment');
    checks.push('Ctrl+S works inside description, R approves once, Shift+R rejects with current comment');
    await description().fill('未保存的草稿');await blur();const before=writes.length;await page.keyboard.press('r');await page.keyboard.press('ArrowRight');
    const warning=page.getByRole('dialog',{name:'有未保存的标注',exact:true});await warning.waitFor();
    await page.keyboard.press('Control+s');if(writes.length!==before)throw Error('Dirty review or modal shortcut wrote data');
    await warning.getByRole('button',{name:'继续编辑',exact:true}).click();
    await warning.waitFor({state:'hidden'});
    if(!page.url().includes('/asset-0?')||await description().inputValue()!=='未保存的草稿')throw Error('Navigation discarded draft');
    failSave=true;await description().press('Control+s');await page.locator('.ant-alert').getByText('保存冲突测试',{exact:true}).waitFor();
    if(await description().inputValue()!=='未保存的草稿')throw Error('Failed save lost draft');
    checks.push('Dirty navigation asks before leaving, dirty review is blocked, modal suppresses shortcuts and failed save retains draft');
    failSave=false;await description().press('Control+s');await page.getByText('有未保存修改',{exact:true}).waitFor({state:'hidden'});
    await blur();await page.getByRole('button',{name:'快捷键',exact:true}).click();
    const help=page.getByRole('dialog',{name:'标注快捷键',exact:true});await help.waitFor();
    const count=writes.length;await page.keyboard.press('r');await page.keyboard.press('Control+s');if(writes.length!==count)throw Error('Help modal did not suppress shortcuts');
    await page.screenshot({path:'output/playwright/caption-shortcuts.png',animations:'disabled'});
    await page.keyboard.press('Escape');await help.waitFor({state:'hidden'});
    for(const task of ['vector','classification']){project.task_type=task;await page.goto(origin+'/annotate/asset-1');await waitAsset(1);await blur();await page.keyboard.press('d');await page.waitForURL(url=>url.pathname==='/annotate/asset-2');await waitAsset(2);}
    checks.push('Shared shortcuts also work for vector descriptions and classification projects');
    completed=true;return {checks,fixtureWrites:writes.length,businessWrites:0};
  }catch(error){await page.screenshot({path:'output/playwright/caption-shortcuts-error.png',animations:'disabled'});throw Error(error.message+'; checks '+checks.join('; ')+'; url '+page.url());}
  finally{if(completed)await page.goto('about:blank');await page.unrouteAll({behavior:'ignoreErrors'});}
}
