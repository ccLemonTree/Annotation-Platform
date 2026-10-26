async page=>{
  await page.unrouteAll();page.setDefaultTimeout(10000);await page.setViewportSize({width:1440,height:1000});
  const origin='http://127.0.0.1:3000',checks=[],errors=[];
  await page.addInitScript(()=>{if(location.origin==='http://127.0.0.1:3000')localStorage.setItem('user',JSON.stringify({id:1,tenant_id:1,username:'fixture',role:'admin'}));});
  const source=await (await page.request.get(origin+'/src/pages/DetectionWorkspace.tsx')).text();
  const react=source.match(/["'](\/node_modules\/\.vite\/deps\/react\.js\?[^"']+)["']/)[1],router=source.match(/["'](\/node_modules\/\.vite\/deps\/react-router-dom\.js\?[^"']+)["']/)[1];
  const project={id:'11111111-1111-4111-8111-111111111111',project_name:'项目类别清理测试',task_type:'detection',label_schema:{labels:['person','bike','ele']}};
  const assets=Array.from({length:3},(_,i)=>({id:'aaaaaaaa-0000-4000-8000-'+String(i).padStart(12,'0'),project_id:project.id,original_name:'fixture-'+i+'.jpg',width:640,height:480,source_type:'upload'}));
  const box={label:'person',x:10,y:20,width:50,height:60},bike={...box,label:'bike',x:100};
  const versions=assets.map((asset,i)=>[{id:'version-'+i,asset_id:asset.id,version:1,is_latest:true,review_status:'approved',annotation_data:{boxes:i===0?[bike,box,box]:i===1?[box]:[bike]}}]);
  let role='admin',token='a'.repeat(64),stale=true,deleted=0,deleteRequests=0,previews=0,reviews=0;
  const recordError=error=>errors.push(error.message);page.on('pageerror',recordError);
  await page.route('**/src/App.tsx*',route=>route.fulfill({contentType:'application/javascript',body:`import React from '${react}';import{createBrowserRouter,RouterProvider}from'${router}';import Provider from '/src/ui/PlatformProvider.tsx';import Layout from '/src/layouts/MainLayout.tsx';import Workspace from '/src/pages/DetectionWorkspace.tsx';import{CurrentUser}from'/src/components/currentUser.ts';const router=createBrowserRouter([{element:React.createElement(CurrentUser.Provider,{value:{id:1,tenant_id:1,username:'fixture',role:'${role}'}},React.createElement(Provider,null,React.createElement(Layout))),children:[{path:'/annotate/:assetId',element:React.createElement(Workspace)}]}]);export default function App(){return React.createElement(RouterProvider,{router});}`}));
  await page.route('**/api/**',async route=>{
    const req=route.request(),url=new URL(req.url()),path=url.pathname,method=req.method();
    if(!path.startsWith('/api/'))return route.continue();
    if(path.startsWith('/api/analytics/'))return route.fulfill({json:{}});
    if(path==='/api/model-services')return route.fulfill({json:[]});
    if(path==='/api/projects/'+project.id)return route.fulfill({json:project});
    if(path.endsWith('/annotations/remove-label/preview')){
      previews++;const label=req.postDataJSON().label,matched=versions.map(rows=>rows[0]).filter(row=>row.annotation_data.boxes.some(box=>box.label===label));
      return route.fulfill({json:{project_id:project.id,project_name:project.project_name,label,scope:'all',total_images:3,affected_images:matched.length,
        removed_boxes:matched.reduce((n,row)=>n+row.annotation_data.boxes.filter(box=>box.label===label).length,0),empty_images:matched.filter(row=>row.annotation_data.boxes.every(box=>box.label===label)).length,
        reviewed_images:matched.filter(row=>row.review_status==='approved').length,preview_token:token}});
    }
    if(path.endsWith('/annotations/remove-label')){
      deleteRequests++;const body=req.postDataJSON();
      if(stale){stale=false;token='b'.repeat(64);return route.fulfill({status:409,json:{detail:'项目标注或范围已变化，请重新预览后再删除'}});}
      if(body.label!=='person'||body.preview_token!==token||Object.keys(body).length!==2)throw Error('Deletion payload changed scope or label');
      for(const rows of versions){const old=rows[0],kept=old.annotation_data.boxes.filter(box=>box.label!=='person');if(kept.length===old.annotation_data.boxes.length)continue;
        old.is_latest=false;rows.unshift({...old,id:old.id+'-new',version:2,is_latest:true,review_status:'pending',annotation_data:{boxes:kept}});}
      deleted++;return route.fulfill({json:{project_id:project.id,label:'person',affected_images:2,removed_boxes:3}});
    }
    const matching=assets.filter((asset,i)=>!url.searchParams.get('label')||versions[i][0].annotation_data.boxes.some(box=>box.label===url.searchParams.get('label')));
    if(path.startsWith('/api/assets/')){
      const asset=assets.find(asset=>asset.id===path.split('/')[3]);if(!asset)throw Error('Unknown fixture asset');
      if(path.endsWith('/content'))return route.fulfill({contentType:'image/png',body:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jUKcAAAAASUVORK5CYII=','base64')});
      if(path.endsWith('/neighbors')){const i=matching.indexOf(asset);return route.fulfill({json:{position:i+1,total:matching.length,previous_id:matching[i-1]?.id||null,next_id:matching[i+1]?.id||null}});}
      return route.fulfill({json:asset});
    }
    if(path.endsWith('/events'))return route.fulfill({json:deleted?[{id:'event',user_id:1,action:'update',detail:{version:2,source:'bulk_label_delete'},created_at:new Date().toISOString()}]:[]});
    if(path==='/api/annotations'&&method==='GET')return route.fulfill({json:{items:versions[assets.findIndex(asset=>asset.id===url.searchParams.get('asset_id'))],total:deleted?2:1}});
    if(path.endsWith('/review')){reviews++;return route.fulfill({json:{}});}
    throw Error('Unexpected fixture request '+method+' '+path);
  });
  const canvas=page.getByRole('img',{name:'检测画框区域'}),button=page.getByRole('button',{name:'批量删除类别框',exact:true});
  const modal=()=>page.getByRole('dialog',{name:'批量删除项目类别框',exact:true});
  const confirm=()=>modal().getByRole('button',{name:/确认删除$/});
  const selectLabel=async value=>{await modal().getByRole('combobox',{name:'待删除框类别',exact:true}).click();await page.getByText(value,{exact:true}).last().click();};
  try{
    await page.goto(origin+'/annotate/'+assets[0].id+'?label=person');await canvas.waitFor();
    await button.click();if(await confirm().isEnabled())throw Error('Confirmation enabled before preview');
    await modal().getByText(/全部标注图片/).waitFor();await modal().getByRole('button',{name:/^取\s*消$/}).click();
    if(deleteRequests||previews)throw Error('Cancel mutated or queried deletion state');checks.push('cancel before preview makes no deletion request');
    await button.click();await modal().getByRole('button',{name:/预览影响$/}).click();
    await modal().getByText(/将从.*2.*张图片中删除.*3.*个.*person.*框/).waitFor();
    await page.waitForFunction(()=>Array.from(document.querySelectorAll('.ant-modal button')).some(button=>button.textContent?.trim()==='确认删除'&&!button.disabled));
    await page.screenshot({path:'output/playwright/project-label-removal-preview.png'});
    await canvas.focus();await page.keyboard.press('r');if(reviews)throw Error('Keyboard review ran behind deletion dialog');
    await selectLabel('bike');if(await confirm().isEnabled())throw Error('Changing label retained old preview');await selectLabel('person');
    await modal().getByRole('button',{name:/预览影响$/}).click();checks.push('preview covers whole project, reports counts and negative samples, and resets when label changes');
    await confirm().click();await modal().getByText('项目标注或范围已变化，请重新预览后再删除',{exact:true}).waitFor();
    if(await confirm().isEnabled()||deleted)throw Error('Stale preview did not block deletion');checks.push('409 retains current boxes and requires a fresh preview');
    await modal().getByRole('button',{name:/预览影响$/}).click();await confirm().click();await modal().waitFor({state:'hidden'});
    await page.getByText('框列表（1）',{exact:true}).waitFor();await page.getByText('v2 · 待审核',{exact:true}).waitFor();await page.waitForURL('**/'+assets[0].id);
    await page.getByText('/ 3',{exact:true}).waitFor();if(deleted!==1||deleteRequests!==2)throw Error('Unexpected mutation count');
    checks.push('success refreshes boxes, version, review state and neighbors, clearing the removed-label filter');
    await canvas.focus();await page.keyboard.press('n');await page.getByText('未保存',{exact:true}).waitFor();if(await button.isEnabled())throw Error('Dirty draft can start batch deletion');await page.keyboard.press('Control+z');
    checks.push('unsaved local edits block bulk deletion');
    await page.getByRole('combobox',{name:'标注历史版本',exact:true}).click();await page.getByText('v1 · 已通过',{exact:true}).last().click();
    await page.getByText('框列表（3）',{exact:true}).waitFor();if(await button.isEnabled())throw Error('Historical view can start deletion');await page.getByRole('button',{name:'返回最新版本',exact:true}).click();
    checks.push('previous version retains person boxes and is read-only');
    await button.click();await modal().getByRole('button',{name:/预览影响$/}).click();await modal().getByText('当前项目没有此类别的框，无需删除',{exact:true}).waitFor();
    if(await confirm().isEnabled())throw Error('Zero-match preview permits deletion');
    await page.waitForFunction(()=>document.querySelectorAll('.ant-message-notice').length===0);await page.screenshot({path:'output/playwright/project-label-removal-desktop.png'});
    await page.setViewportSize({width:390,height:844});await page.waitForFunction(()=>document.documentElement.scrollWidth<=innerWidth+1);
    const bounds=await confirm().boundingBox();if(!bounds||bounds.y+bounds.height>844)throw Error('Mobile confirmation is outside viewport');
    await page.screenshot({path:'output/playwright/project-label-removal-mobile.png'});checks.push('zero-match confirmation is disabled and dialog fits mobile viewport');
    await modal().getByRole('button',{name:/^取\s*消$/}).click();role='annotator';await page.reload();await canvas.waitFor();if(await button.count())throw Error('Non-admin sees bulk deletion button');
    checks.push('non-admin does not see deletion controls');
    if(errors.length)throw Error(errors.join('; '));
    return {passed:true,checks,preview_requests:previews,delete_requests:deleteRequests,successful_deletions:deleted,business_writes:0};
  }catch(error){await page.screenshot({path:'output/playwright/project-label-removal-failure.png'});throw error;}
  finally{await page.goto('about:blank');await page.unrouteAll();page.off('pageerror',recordError);}
}


