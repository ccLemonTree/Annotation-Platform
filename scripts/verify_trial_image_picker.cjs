async (page) => {
  page.setDefaultTimeout(10000);
  await page.setViewportSize({width:1440,height:1000});
  const source = await (await page.request.get('http://127.0.0.1:3000/src/pages/ModelServicesPage.tsx')).text();
  const reactUrl = source.match(/["'](\/node_modules\/\.vite\/deps\/react\.js\?[^"']+)["']/)[1];
  const routerUrl = source.match(/["'](\/node_modules\/\.vite\/deps\/react-router-dom\.js\?[^"']+)["']/)[1];
  const checks=[], queries=[], predictions=[];
  const collection='cccccccc-0000-4000-8000-000000000001';
  const imageId=n=>`aaaaaaaa-0000-4000-8000-${String(n).padStart(12,'0')}`;
  const model={id:'11111111-1111-4111-8111-111111111111',key:'groundingpi',name:'GroundingPI',
    kind:'detection',protocol:'model_service',base_url:'http://model.test',endpoint:'/detect/grounding',
    enabled:true,revision:1,labels:[],label_mapping:{},timeout_seconds:180,confidence:.25,iou:.45};
  let releaseSlow, originalStorage, failedOnce=false;
  const apiPattern='**/api/**', appPattern='**/src/App.tsx*';
  await page.route(appPattern,route=>route.fulfill({contentType:'application/javascript',body:`
    import React from '${reactUrl}';
    import {BrowserRouter} from '${routerUrl}';
    import Provider from '/src/ui/PlatformProvider.tsx';
    import ModelServicesPage from '/src/pages/ModelServicesPage.tsx';
    export default function App(){return React.createElement(BrowserRouter,null,React.createElement(Provider,null,React.createElement(ModelServicesPage)));}` }));
  await page.route(apiPattern,async route=>{
    const req=route.request(), url=new URL(req.url()), path=url.pathname;
    if(!path.startsWith('/api/'))return route.continue();
    if(path==='/api/auth/me')return route.fulfill({json:{id:1,role:'admin'}});
    if(path==='/api/model-services/prompt-defaults')return route.fulfill({json:{caption:'caption',review:'review'}});
    if(path==='/api/model-services')return route.fulfill({json:[model]});
    if(path==='/api/images/collections')return route.fulfill({json:[{id:collection,name:'历史图片集',image_count:45}]});
    if(path==='/api/images'){
      const query=Object.fromEntries(url.searchParams); queries.push(query);
      if(query.q==='slow')await new Promise(resolve=>{releaseSlow=resolve;});
      if(query.q==='failure'&&!failedOnce){failedOnce=true;return route.fulfill({status:503,json:{detail:'unavailable'}});}
      const current=Number(query.page||1), size=Number(query.page_size||20);
      const start=(current-1)*size;
      const items=query.q==='slow'?[{id:imageId(999),original_name:'迟到结果.jpg'}]:Array.from({length:Math.min(size,45-start)},(_,i)=>({id:imageId(start+i+1),original_name:`历史图片-${start+i+1}.jpg`}));
      return route.fulfill({json:{items,total:45,page:current,page_size:size}});
    }
    if(path.endsWith('/predict')){
      predictions.push(req.postDataJSON());
      return route.fulfill({json:{kind:'detection',boxes:[],warnings:[],model_service_id:model.id,config_revision:1}});
    }
    if(path.endsWith('/content'))return route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg"/>'});
    throw Error('Unexpected API '+req.method()+' '+path);
  });
  const choose=async(label,text)=>{
    await page.getByRole('combobox',{name:label,exact:true}).click();
    await page.locator('.ant-select-dropdown:visible .ant-select-item-option-content').filter({hasText:text}).click();
  };
  const waitQuery=async predicate=>{
    for(let i=0;i<100;i++){if(queries.length&&predicate(queries.at(-1)))return;await new Promise(resolve=>setTimeout(resolve,30));}
    throw Error('Expected image query; received '+JSON.stringify(queries));
  };
  try{
    await page.goto('http://127.0.0.1:3000/');
    originalStorage=await page.evaluate(()=>({token:localStorage.getItem('token'),user:localStorage.getItem('user')}));
    await page.evaluate(()=>{localStorage.setItem('token','trial-picker-fixture');localStorage.setItem('user',JSON.stringify({id:1,role:'admin'}));});
    await page.reload();
    await page.getByRole('row').filter({hasText:'GroundingPI'}).getByRole('button',{name:'试运行',exact:true}).click();
    await page.getByRole('combobox',{name:'试运行图片集',exact:true}).waitFor();
    const dialog=page.getByRole('dialog',{name:'模型试运行 · GroundingPI'});
    const pagination=page.getByLabel('试运行图片分页',{exact:true});
    await pagination.getByText('共 45 张',{exact:true}).waitFor();
    await pagination.locator('.ant-pagination-item-2').click();
    await waitQuery(q=>q.page==='2'&&q.page_size==='20');
    await choose('试运行图片库图片','历史图片-21.jpg');
    if(await page.getByRole('textbox',{name:'图片 ID',exact:true}).inputValue()!==imageId(21))throw Error('Wrong page image selected');
    await page.getByRole('button',{name:'开始试运行',exact:true}).click();
    await dialog.getByText('未检测到目标；不能自动认定为负样本，请人工核对。',{exact:true}).waitFor();
    if(predictions.at(-1).image_id!==imageId(21))throw Error('Trial used a different image');
    checks.push('page 2 selects historical image and predicts the same ID');
    await pagination.getByRole('combobox').click();
    await page.locator('.ant-select-dropdown:visible .ant-select-item-option-content').filter({hasText:'50 条/页'}).click();
    await waitQuery(q=>q.page==='1'&&q.page_size==='50');
    if(await page.getByRole('textbox',{name:'图片 ID',exact:true}).inputValue())throw Error('Page size change kept old image');
    await pagination.getByRole('combobox').click();
    await page.locator('.ant-select-dropdown:visible .ant-select-item-option-content').filter({hasText:'20 条/页'}).click();
    await waitQuery(q=>q.page==='1'&&q.page_size==='20');
    await pagination.locator('.ant-pagination-item-2').click();
    await waitQuery(q=>q.page==='2');
    checks.push('page size changes reset page and selection');
    await choose('试运行图片集','历史图片集');
    await waitQuery(q=>q.collection_id===collection&&q.page==='1');
    if(await page.getByRole('textbox',{name:'图片 ID',exact:true}).inputValue())throw Error('Old selection survived collection change');
    await page.getByRole('searchbox',{name:'试运行图片名称',exact:true}).fill('old-camera');
    await page.getByRole('searchbox',{name:'试运行图片名称',exact:true}).press('Enter');
    await waitQuery(q=>q.collection_id===collection&&q.q==='old-camera'&&q.page==='1');
    await page.getByPlaceholder('入库开始时间',{exact:true}).fill('2026-10-01 00:00:00');
    await page.getByPlaceholder('入库开始时间',{exact:true}).press('Enter');
    await page.getByPlaceholder('入库结束时间',{exact:true}).fill('2026-10-19 23:59:59');
    await page.getByPlaceholder('入库结束时间',{exact:true}).press('Enter');
    const confirm=page.locator('.ant-picker-dropdown:visible').getByRole('button',{name:'确定',exact:true});
    if(await confirm.isVisible())await confirm.click();
    await waitQuery(q=>!!q.created_from&&!!q.created_to&&q.q==='old-camera'&&q.collection_id===collection);
    const bounds=await page.evaluate(()=>[new Date('2026-10-01T00:00:00').toISOString(),new Date('2026-10-19T23:59:59').toISOString()]);
    if(queries.at(-1).created_from!==bounds[0]||queries.at(-1).created_to!==bounds[1])throw Error('Date range lost timezone conversion');
    checks.push('collection, filename and date range compose with UTC bounds');
    await pagination.locator('.ant-pagination-item-2').click();
    await waitQuery(q=>q.page==='2'&&!!q.created_from&&q.collection_id===collection);
    await page.getByRole('searchbox',{name:'试运行图片名称',exact:true}).fill('slow');
    await page.getByRole('searchbox',{name:'试运行图片名称',exact:true}).press('Enter');
    await waitQuery(q=>q.q==='slow'&&q.page==='1');
    await page.getByRole('searchbox',{name:'试运行图片名称',exact:true}).fill('latest-query');
    await page.getByRole('searchbox',{name:'试运行图片名称',exact:true}).press('Enter');
    await waitQuery(q=>q.q==='latest-query');
    releaseSlow();releaseSlow=undefined;
    await page.waitForLoadState('networkidle');
    await choose('试运行图片库图片','历史图片-1.jpg');
    if(await page.getByRole('textbox',{name:'图片 ID',exact:true}).inputValue()!==imageId(1))throw Error('Stale query corrupted selection');
    await page.getByRole('textbox',{name:'图片 ID',exact:true}).click();
    await page.locator('.ant-select-dropdown:visible').waitFor({state:'hidden'});
    checks.push('search resets page and late results do not overwrite current results');
    await page.screenshot({path:'output/playwright/trial-image-picker-desktop.png'});
    await page.setViewportSize({width:390,height:844});
    await page.screenshot({path:'output/playwright/trial-image-picker-mobile.png'});
    if(await dialog.evaluate(node=>node.scrollWidth>node.clientWidth+2))throw Error('Picker overflows the mobile dialog');
    checks.push('desktop and narrow-screen dialog fit');
    await page.getByRole('searchbox',{name:'试运行图片名称',exact:true}).fill('failure');
    await page.getByRole('searchbox',{name:'试运行图片名称',exact:true}).press('Enter');
    await dialog.getByText('图片列表读取失败，请重试',{exact:true}).waitFor();
    if(await page.getByRole('textbox',{name:'图片 ID',exact:true}).inputValue())throw Error('Failed query kept stale selection');
    await dialog.getByRole('button',{name:'重试图片列表',exact:true}).click();
    await dialog.getByText('图片列表读取失败，请重试',{exact:true}).waitFor({state:'hidden'});
    checks.push('read failure clears selection and supports retry');
    return {checks,imageQueries:queries.length,predictions:predictions.length,annotationWrites:0};
  }finally{
    releaseSlow?.();
    if(originalStorage)await page.evaluate(storage=>{for(const[k,v]of Object.entries(storage)){if(v===null)localStorage.removeItem(k);else localStorage.setItem(k,v);}},originalStorage);
    await page.unroute(apiPattern);await page.unroute(appPattern);
  }
}
