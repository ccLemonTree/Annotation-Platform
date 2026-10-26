async page=>{
  page.setDefaultTimeout(8000);const origin='http://127.0.0.1:3000',checks=[],requests=[];
  const source=await(await page.request.get(origin+'/src/pages/ValidationPage.tsx')).text();
  const react=source.match(/["'](\/node_modules\/\.vite\/deps\/react\.js\?[^"']+)["']/)[1],router=source.match(/["'](\/node_modules\/\.vite\/deps\/react-router-dom\.js\?[^"']+)["']/)[1];
  const id='aaaaaaaa-0000-4000-8000-000000000021';let role='admin',removed=false,fail=true,deletes=0;
  const run={id,name:'删除验收任务',status:'running',total:1,processed:0,failed_count:0,remote_active:true,model:{name:'RF-DETR',revision:1,source:'checkpoint'},config:{confidence:.25,match_iou:.5,inference_confidence:.2,nms_iou:.45},metrics:{coverage:{total:1,succeeded:0,failed:0,pending:1,canceled:0,complete:false}},updated_at:'2026-10-20T07:00:00Z'};
  const app='**/src/App.tsx*',api='**/api/**';
  await page.route(app,route=>route.fulfill({contentType:'application/javascript',body:`import React from '${react}';import{createBrowserRouter,RouterProvider}from'${router}';import Provider from '/src/ui/PlatformProvider.tsx';import Page from '/src/pages/ValidationPage.tsx';const router=createBrowserRouter([{path:'/validation',element:React.createElement(Provider,null,React.createElement(Page))}]);export default function App(){return React.createElement(RouterProvider,{router});}`}));
  await page.route(api,route=>{
    const req=route.request(),url=new URL(req.url()),path=url.pathname;
    if(!path.startsWith('/api/'))return route.continue();
    requests.push({method:req.method(),path,page:url.searchParams.get('page')});
    if(req.method()==='DELETE'){
      if(path!=='/api/validation/runs/'+id)throw Error('Wrong delete target');deletes++;
      if(fail)return route.fulfill({status:409,json:{detail:'远端验证停止尚未确认'}});
      removed=true;return route.fulfill({status:204});
    }
    if(req.method()!=='GET')throw Error('Unexpected write '+path);
    if(path==='/api/auth/me')return route.fulfill({json:{id:1,role}});
    if(path==='/api/projects'||path==='/api/validation/sets')return route.fulfill({json:{items:[],total:0,page:1,page_size:20}});
    if(path==='/api/validation/runs'){
      const current=Number(url.searchParams.get('page')||1);
      const items=current===2?(removed?[]:[run]):Array.from({length:20},(_,i)=>({...run,id:'other-'+i,name:'保留任务 '+i,status:'completed',remote_active:false}));
      return route.fulfill({json:{items,total:removed?20:21,page:current,page_size:20}});
    }
    if(path.endsWith('/items'))return route.fulfill({json:{items:[],total:0,page:1,page_size:20}});
    if(path.endsWith('/'+id))return route.fulfill({json:{...run,target_metrics:{images:0,per_class:[]}}});
    throw Error('Unexpected read '+path);
  });
  try{
    await page.setViewportSize({width:1440,height:1000});await page.goto(origin+'/validation');
    await page.locator('.ant-pagination-item-2').last().click();await page.getByText('删除验收任务',{exact:true}).waitFor();
    await page.getByRole('button',{name:'查看结果',exact:true}).click();await page.getByText('验证报告',{exact:true}).waitFor();
    await page.getByText('准备、上传或批量推理期间暂为 0',{exact:true}).waitFor();
    await page.getByText('正在准备数据、上传或批量推理，尚未入库结果',{exact:true}).waitFor();
    await page.getByRole('button',{name:'删除任务',exact:true}).click();await page.getByRole('dialog').waitFor();
    await page.getByText('将先取消任务并确认远端验证停止；停止失败时保留任务，请重试。',{exact:true}).waitFor();
    await page.getByRole('button',{name:'保留任务',exact:true}).click();if(deletes)throw Error('Cancel sent DELETE');
    checks.push('Running task deletion warns about stop; cancel never sends DELETE');
    await page.getByRole('button',{name:'删除任务',exact:true}).click();await page.getByRole('button',{name:'确认删除任务',exact:true}).click();
    await page.getByRole('dialog').getByText('任务操作失败，请刷新后重试：远端验证停止尚未确认',{exact:true}).waitFor();
    if(removed)throw Error('Failure removed record');checks.push('Failed stop keeps task and confirmation with error');
    await page.screenshot({path:'output/playwright/validation-delete-confirm.png'});
    await page.getByRole('button',{name:'保留任务',exact:true}).click();fail=false;
    await page.getByRole('button',{name:'删除任务',exact:true}).click();await page.getByRole('button',{name:'确认删除任务',exact:true}).click();
    await page.getByRole('dialog').waitFor({state:'hidden'});await page.getByText('保留任务 0',{exact:true}).waitFor();
    if(await page.getByText('验证报告',{exact:true}).count())throw Error('Deleted report remains open');
    if(deletes!==2||!requests.some(r=>r.path==='/api/validation/runs'&&r.page==='1'))throw Error('Deletion did not return to valid page');
    checks.push('Successful deletion closes selected report, refreshes list and returns from empty last page');
    role='annotator';await page.reload();await page.getByText('只读模式：创建、取消、重试和删除仅管理员可操作。',{exact:true}).waitFor();
    if(await page.getByRole('button',{name:'删除任务',exact:true}).count())throw Error('Read-only user can delete');
    checks.push('Read-only users have no delete action');return {checks,mockedDeletes:deletes,realTaskDeletes:0};
  }finally{await page.goto('about:blank');await page.unroute(api);await page.unroute(app);}
}
