async page=>{
  page.setDefaultTimeout(7000);
  await page.setViewportSize({width:1440,height:1000});
  const origin='http://127.0.0.1:3000',checks=[];
  const source=await(await page.request.get(origin+'/src/pages/AssetsPage.tsx')).text();
  const react=source.match(/["'](\/node_modules\/\.vite\/deps\/react\.js\?[^"']+)["']/)[1];
  const router=source.match(/["'](\/node_modules\/\.vite\/deps\/react-router-dom\.js\?[^"']+)["']/)[1];
  let count=844,reads=0,status='uploading';
  const config={classification_enabled:false,min_long_edge:0,scenes:[],base_url:'http://model.test/v1',model:'fixture'};
  const group={id:'11111111-1111-4111-8111-111111111111',name:'验证数据',ingest_config:config};
  const api='**/api/**',app='**/src/App.tsx*';
  await page.route(app,route=>route.fulfill({contentType:'application/javascript',body:`import React from '${react}';import{BrowserRouter}from'${router}';import Provider from '/src/ui/PlatformProvider.tsx';import AssetsPage from '/src/pages/AssetsPage.tsx';export default function App(){return React.createElement(BrowserRouter,null,React.createElement(Provider,null,React.createElement(AssetsPage)));}`}));
  await page.route(api,async route=>{
    const req=route.request(),url=new URL(req.url()),path=url.pathname;
    if(!path.startsWith('/api/'))return route.continue();
    if(req.method()!=='GET')throw Error('Unexpected write '+path);
    if(path==='/api/images/collections')return route.fulfill({json:[{...group,image_count:count}]});
    if(path==='/api/projects')return route.fulfill({json:{items:[],total:0}});
    if(path==='/api/images/ingest-settings')return route.fulfill({json:config});
    if(path==='/api/images')return route.fulfill({json:{items:[{id:'22222222-2222-4222-8222-222222222222',original_name:'fixture.jpg',width:64,height:48,file_size:100,source_type:'upload',scene_status:'unclassified',scene_labels:[],collection_names:['验证数据'],created_at:'2026-10-20T00:00:00Z'}],total:url.searchParams.get('q')?2:count,page:Number(url.searchParams.get('page')||1),page_size:20}});
    if(path.endsWith('/content'))return route.fulfill({contentType:'image/png',body:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jUKcAAAAASUVORK5CYII=','base64')});
    if(path==='/api/image-uploads/config')return route.fulfill({json:{chunk_size:8388608,max_file_size:10*1024**3,max_image_size:100*1024**2,max_expanded_size:20*1024**3,max_entries:10000,retention_days:7}});
    if(path==='/api/image-uploads'){
      reads++;return route.fulfill({json:[{id:'33333333-3333-4333-8333-333333333333',filename:'validation.zip',collection_id:group.id,size:1000,uploaded_bytes:status==='uploading'?770:1000,status,min_long_edge:0,cursor:status==='uploading'?0:704,result:{imported:status==='uploading'?0:704,duplicates:0,filtered:0,invalid:0,ignored:0,errors:[]}}]});
    }
    throw Error('Unexpected API '+path);
  });
  try{
    await page.goto(origin+'/assets');
    const picker=page.getByRole('combobox',{name:'筛选图片集',exact:true});await picker.click();await page.getByText('验证数据（844）',{exact:true}).last().click();
    await page.getByText('验证数据（844）',{exact:true}).last().waitFor();
    count=2267;await page.locator('.ant-pagination-item-2').first().click();
    await page.getByText('共 2267 张',{exact:true}).first().waitFor();
    await page.getByText('验证数据（2267）',{exact:true}).last().waitFor();
    checks.push('Pagination refreshes stale collection count from 844 to 2267 with the image list');
    await page.screenshot({path:'output/playwright/collection-count-refresh.png'});
    await page.getByRole('searchbox',{name:'筛选图片名称',exact:true}).fill('filtered');await page.getByRole('searchbox',{name:'筛选图片名称',exact:true}).press('Enter');
    await page.getByText('共 2 张',{exact:true}).first().waitFor();
    if(!await page.getByText('验证数据（2267）',{exact:true}).last().isVisible())throw Error('Filtered total replaced full collection count');
    checks.push('Collection count remains the full collection size when filename filtering reduces the table');
    await page.locator('.page-toolbar-actions').getByRole('button',{name:/上传图片/}).click();
    const dialog=page.getByRole('dialog',{name:'上传到图片库',exact:true});await dialog.getByText('等待续传',{exact:true}).waitFor();
    status='processing';await dialog.getByText('正在处理',{exact:true}).waitFor();
    await dialog.getByText('100%',{exact:true}).waitFor();await dialog.getByText(/入库 704 张/).waitFor();
    await page.screenshot({path:'output/playwright/upload-status-refresh.png'});
    checks.push('Upload history polls server state, received bytes and imported count while dialog stays open');
    status='completed';await dialog.getByText('已完成',{exact:true}).waitFor();
    await dialog.getByRole('button',{name:'关 闭',exact:true}).click();await dialog.waitFor({state:'hidden'});
    const stopped=reads;await page.waitForTimeout(2300);if(reads!==stopped)throw Error('History polling continued after close');
    checks.push('Completion updates without manual refresh; history polling stops on close');
    return {checks,historyReads:reads,businessWrites:0};
  }finally{await page.goto('about:blank');await page.unroute(api);await page.unroute(app);}
}
