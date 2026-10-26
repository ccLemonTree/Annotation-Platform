async page=>{
  await page.unrouteAll();page.setDefaultTimeout(8000);
  const origin='http://127.0.0.1:3000',project='aaaaaaaa-1111-4000-8000-000000000001',calls=[],checks=[];
  const source=await(await page.request.get(origin+'/src/pages/AssetsPage.tsx')).text();
  const react=source.match(/["'](\/node_modules\/\.vite\/deps\/react\.js\?[^"']+)["']/)[1];
  const router=source.match(/["'](\/node_modules\/\.vite\/deps\/react-router-dom\.js\?[^"']+)["']/)[1];
  const app='**/src/App.tsx*',api='**/api/**';
  const rows=Array.from({length:45},(_,i)=>({id:'asset-'+i,project_id:project,original_name:(i<25?'fire_cam_':'other_')+i+'.jpg',source_type:i%2?'model_crop':'upload',annotation_status:'reviewed',width:640,height:480,file_size:100,created_at:'2026-10-20T00:00:00Z'}));
  await page.route(app,route=>route.fulfill({contentType:'application/javascript',body:`import React from '${react}';import{BrowserRouter,Routes,Route}from'${router}';import Provider from '/src/ui/PlatformProvider.tsx';import Assets from '/src/pages/AssetsPage.tsx';export default function App(){return React.createElement(Provider,null,React.createElement(BrowserRouter,null,React.createElement(Routes,null,React.createElement(Route,{path:'/projects/:projectId/images',element:React.createElement(Assets)}))));}`}));
  await page.route(api,route=>{
    const req=route.request(),url=new URL(req.url()),path=url.pathname;
    if(!path.startsWith('/api/'))return route.continue();
    if(req.method()!=='GET')throw Error('Unexpected write '+path);
    calls.push({path,query:Object.fromEntries(url.searchParams)});
    if(path==='/api/projects/'+project)return route.fulfill({json:{id:project,project_name:'文件名搜索验证',task_type:'detection',label_schema:{labels:['fire'],annotation_scope:'all'}}});
    if(path.endsWith('/content'))return route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="640" height="480"/>'});
    if(path==='/api/assets'||path==='/api/assets/selection'){
      const q=(url.searchParams.get('q')||'').toLowerCase(),kind=url.searchParams.get('image_kind');
      const filtered=rows.filter(row=>row.original_name.toLowerCase().includes(q)&&(!kind||kind==='all'||(kind==='crops')===(row.source_type==='model_crop')));
      const pageNumber=Number(url.searchParams.get('page')||1),size=Number(url.searchParams.get('page_size')||20);
      return route.fulfill({headers:{'X-Asset-Filename-Filter':'1'},json:path.endsWith('/selection')?{asset_ids:filtered.map(row=>row.id),total:filtered.length}:{items:filtered.slice((pageNumber-1)*size,pageNumber*size),total:filtered.length,page:pageNumber,page_size:size}});
    }
    throw Error('Unexpected read '+path);
  });
  const input=()=>page.getByLabel('项目图片文件名',{exact:true});
  try{
    await page.setViewportSize({width:1440,height:1000});await page.goto(origin+'/projects/'+project+'/images');
    await page.getByText('共 45 张',{exact:true}).first().waitFor();
    await page.getByTitle('2',{exact:true}).first().click();await page.getByText('other_25.jpg',{exact:true}).waitFor();
    await input().fill(' FIRE_CAM_ ');await input().press('Enter');
    await page.getByText('共 25 张',{exact:true}).first().waitFor();await page.getByText('fire_cam_0.jpg',{exact:true}).waitFor();
    if(!calls.some(c=>c.path==='/api/assets'&&c.query.q==='FIRE_CAM_'&&c.query.page==='1'))throw Error('Search did not trim and reset page');
    checks.push('Partial-name search submits on Enter, trims whitespace and resets from page 2 to page 1');
    await page.getByRole('checkbox',{name:'Select all',exact:true}).check();
    await input().fill('fire_cam_2');await page.getByRole('button',{name:/^搜\s*索$/}).click();
    await page.getByText('共 6 张',{exact:true}).first().waitFor();await page.getByRole('button',{name:'删除选中图片（0）',exact:true}).waitFor();
    await page.getByRole('combobox',{name:'项目图片来源',exact:true}).click();await page.locator('.ant-select-dropdown:visible').getByText('仅裁剪图',{exact:true}).click();
    await page.getByText('共 2 张',{exact:true}).first().waitFor();
    await page.getByRole('button',{name:'删除搜索结果图片',exact:true}).click();
    await page.getByText('共 2 张图片，将删除其项目记录及标注。',{exact:true}).waitFor();
    if(!calls.some(c=>c.path.endsWith('/selection')&&c.query.q==='fire_cam_2'&&c.query.image_kind==='crops'))throw Error('Selection lost search conditions');
    await page.getByRole('dialog').getByRole('button',{name:/^取\s*消$/}).click();
    checks.push('Search clears selection, combines with crop filter and bounds bulk-operation scope without deleting');
    await input().fill('not-found');await input().press('Enter');await page.locator('.ant-table-placeholder').getByText(/当前筛选下暂无图片/).waitFor();
    await input().fill('');await page.getByText('共 22 张',{exact:true}).first().waitFor();
    await page.getByRole('combobox',{name:'项目图片来源',exact:true}).click();await page.locator('.ant-select-dropdown:visible').getByText('全部项目图片',{exact:true}).click();
    await input().fill('fire_cam_');await input().press('Enter');await page.getByText('共 25 张',{exact:true}).first().waitFor();
    await page.getByRole('button',{name:'预览 fire_cam_19.jpg',exact:true}).click();
    await page.getByRole('button',{name:'下一张',exact:true}).click();
    await page.getByText('21 / 25',{exact:true}).waitFor();
    if(!calls.some(c=>c.path==='/api/assets'&&c.query.q==='fire_cam_'&&c.query.page==='2'))throw Error('Cross-page preview lost search');
    await page.keyboard.press('Escape');
    checks.push('No-match, clear search and filtered preview across page boundary work');
    await page.screenshot({path:'output/playwright/project-filename-desktop.png',animations:'disabled'});
    await page.setViewportSize({width:390,height:844});await page.screenshot({path:'output/playwright/project-filename-mobile.png',animations:'disabled'});
    return {checks,businessWrites:0};
  }finally{await page.goto('about:blank');await page.unrouteAll({behavior:'ignoreErrors'});}
}
