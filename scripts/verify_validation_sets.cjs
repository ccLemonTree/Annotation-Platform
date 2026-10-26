async page=>{
  await page.unrouteAll();page.setDefaultTimeout(10000);
  const origin='http://127.0.0.1:3000',writes=[],errors=[];
  const source=await(await page.request.get(origin+'/src/pages/ValidationPage.tsx')).text();
  const react=source.match(/["'](\/node_modules\/\.vite\/deps\/react\.js\?[^"']+)["']/)[1],router=source.match(/["'](\/node_modules\/\.vite\/deps\/react-router-dom\.js\?[^"']+)["']/)[1];
  const original={id:'set-old',name:'旧验证集',project_id:'project-1',labels:['fire','star'],total:3,source:{kind:'project'},created_at:new Date().toISOString()};
  const updated={...original,id:'set-new',name:'旧验证集 · 更新标注',labels:['smoke'],source:{kind:'project',refreshed_from_set_id:'set-old'}};
  let sets=[original],role='admin',refreshFail=true,deleteFail=true;
  await page.route('**/src/App.tsx*',route=>route.fulfill({contentType:'application/javascript',body:`import React from '${react}';import{BrowserRouter}from'${router}';import Provider from '/src/ui/PlatformProvider.tsx';import Page from '/src/pages/ValidationPage.tsx';export default function App(){return React.createElement(BrowserRouter,null,React.createElement(Provider,null,React.createElement(Page)));}`}));
  await page.route('**/api/**',route=>{
    const req=route.request(),path=new URL(req.url()).pathname;
    if(!path.startsWith('/api/'))return route.continue();
    const reply=json=>route.fulfill({json});
    if(path==='/api/auth/me')return reply({id:1,role});
    if(path==='/api/projects')return reply({items:[{id:'project-1',project_name:'验证项目',task_type:'detection'}],total:1});
    if(path==='/api/validation/sets')return reply({items:sets,total:sets.length});
    if(path==='/api/validation/runs')return reply({items:[],total:0});
    writes.push({method:req.method(),path});
    if(path==='/api/validation/sets/set-old/refresh'&&req.method()==='POST'){
      if(refreshFail)return route.fulfill({status:400,json:{detail:'所选图片必须全部经过人工审核通过'}});
      sets=[updated,original];return reply(updated);
    }
    if(path==='/api/validation/sets/set-new'&&req.method()==='DELETE'){
      if(deleteFail)return route.fulfill({status:409,json:{detail:'验证集仍有运行中的任务'}});
      sets=[original];return route.fulfill({status:204});
    }
    throw Error('Unexpected '+req.method()+' '+path);
  });
  page.on('pageerror',e=>errors.push(e.message));
  const row=name=>page.getByRole('row').filter({has:page.getByText(name,{exact:true})});
  try{
    await page.goto(origin+'/validation');await page.setViewportSize({width:1440,height:1100});
    await row(original.name).getByRole('button',{name:'更新标注',exact:true}).click();
    const refresh=page.getByRole('dialog',{name:'更新验证集标注？',exact:true});
    await refresh.getByRole('button',{name:'生成新版验证集',exact:true}).click();
    await refresh.getByText(/所选图片必须全部经过人工审核通过/).waitFor();
    if(!await row(original.name).isVisible())throw Error('Failed refresh removed old set');
    refreshFail=false;await page.locator('.ant-modal:visible').getByRole('button',{name:/生成新版验证集$/}).click();await refresh.waitFor({state:'hidden'});
    await row(updated.name).getByText('smoke',{exact:true}).waitFor();await row(original.name).getByText('fire、star',{exact:true}).waitFor();
    await row(updated.name).getByRole('button',{name:'删除验证集',exact:true}).click();
    const removal=page.getByRole('dialog',{name:'删除验证集？',exact:true});
    await removal.getByRole('button',{name:'确认删除验证集',exact:true}).click();
    await removal.getByText(/验证集仍有运行中的任务/).waitFor();
    deleteFail=false;await page.locator('.ant-modal:visible').getByRole('button',{name:/确认删除验证集$/}).click();await removal.waitFor({state:'hidden'});
    await row(updated.name).waitFor({state:'hidden'});await row(original.name).waitFor();
    await page.screenshot({path:'output/playwright/validation-set-actions.png',fullPage:true,animations:'disabled'});
    role='annotator';await page.reload();await page.getByText('只读模式：创建、取消、重试和删除仅管理员可操作。',{exact:true}).waitFor();
    if(await page.getByRole('button',{name:'更新标注',exact:true}).count()||await page.getByRole('button',{name:'删除验证集',exact:true}).count())throw Error('Read-only user sees write actions');
    if(errors.length)throw Error(errors.join(';'));
    return {checks:['Refresh error retains original set and offers retry','New snapshot displays latest labels and old snapshot remains unchanged','Deletion confirms scope and retains record on active-job error','Successful deletion refreshes list','Read-only user has no set write actions'],fixtureWrites:writes.length,businessWrites:0};
  }catch(error){await page.screenshot({path:'output/playwright/validation-set-actions-error.png',fullPage:true});throw Error(error.message+' writes='+JSON.stringify(writes)+' dialogs='+JSON.stringify(await page.locator('[role="dialog"]').evaluateAll(nodes=>nodes.map(node=>({label:node.getAttribute('aria-labelledby'),name:document.getElementById(node.getAttribute('aria-labelledby'))?.textContent,text:node.textContent,buttons:[...node.querySelectorAll('button')].map(b=>({text:b.textContent,aria:b.getAttribute('aria-label'),disabled:b.disabled,html:b.outerHTML}))})))));}
  finally{await page.goto('about:blank');await page.unrouteAll({behavior:'ignoreErrors'});}
}
