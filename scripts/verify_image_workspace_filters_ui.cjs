async page=>{
  const base='http://127.0.0.1:3017',pid='11111111-1111-4111-8111-111111111111',a='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',b='bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',collection='cccccccc-cccc-4ccc-8ccc-cccccccccccc';
  const reads=[],errors=[];let reviewed=false,failNeighbors=false;
  const asset=id=>({id,project_id:pid,original_name:'target.png',width:640,height:360,source_type:'model_crop'});
  await page.unrouteAll();page.on('pageerror',e=>errors.push(e.message));page.setDefaultTimeout(7000);
  await page.addInitScript(()=>localStorage.setItem('token','isolated-filter-fixture'));
  await page.route(base+'/api/**',async route=>{
    const req=route.request(),u=new URL(req.url()),p=u.pathname,q=u.searchParams;
    const json=(data,status=200)=>route.fulfill({json:data,status,headers:{'X-Annotation-Label-Filter':'1','X-Annotation-Review-Filter':'1','X-Asset-Collection-Filter':'1'}});
    if(req.method()!=='GET'){
      if(p.endsWith('/review')){reviewed=true;return json({id:'ann',version:1,is_latest:true,review_status:'approved',annotation_data:{labels:['car'],description:''}});}
      throw Error('Unexpected write '+p);
    }
    reads.push(u.href);
    if(p==='/api/auth/me')return json({id:1,role:'admin',username:'fixture',tenant_id:1});
    if(p==='/api/projects/'+pid)return json({id:pid,task_type:'classification',project_name:'分类筛选',label_schema:{labels:['car','person'],classification_mode:'multi'}});
    if(p==='/api/assets/collections')return json([{id:collection,name:'测试图片集'}]);
    if(p==='/api/assets')return json({items:q.get('review_status')==='rejected'?[]:[asset(q.get('page')==='2'?b:a)],total:q.get('review_status')==='rejected'?0:2});
    if(p.endsWith('/neighbors')){
      if(failNeighbors)return json({detail:'fixture failure'},500);
      const second=p.includes(b),unmatched=reviewed&&second&&q.get('review_status')==='pending';
      return json({position:unmatched?0:second?2:1,total:unmatched?1:2,matched:!unmatched,previous_id:second?a:null,next_id:second?null:b});
    }
    if(p.endsWith('/content'))return route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360"><rect width="640" height="360" fill="gray"/></svg>'});
    if(p.startsWith('/api/assets/'))return json(asset(p.split('/')[3]));
    if(p==='/api/annotations')return json({items:[{id:'ann',version:1,is_latest:true,review_status:reviewed&&q.get('asset_id')===b?'approved':'pending',annotation_data:{labels:['car'],description:''}}],total:1});
    return json({detail:'Unexpected '+p},404);
  });
  await page.setViewportSize({width:1440,height:900});await page.goto(base+'/annotate/'+b+'?from=review&project_id='+pid);
  const select=async(name,text)=>{await page.getByRole('combobox',{name,exact:true}).click();await page.locator('.ant-select-dropdown:visible .ant-select-item-option-content').filter({hasText:new RegExp('^'+text+'$')}).click();};
  await select('筛选图片集','测试图片集');await page.waitForURL('**/annotate/'+a+'?**collection_id='+collection);
  await select('筛选标注标签','car');await page.waitForURL('**label=car**');
  await select('筛选审核状态','待审核');await page.waitForURL('**review_status=pending**');
  if(!page.url().includes('from=review')||!page.url().includes('collection_id='+collection)||!page.url().includes('label=car'))throw Error('Combined filters or return context lost');
  await page.getByRole('link',{name:'下一张',exact:true}).click();await page.waitForURL('**/annotate/'+b+'?**');
  await page.getByText('第 2 / 2 张',{exact:true}).waitFor();
  const lastNeighbors=reads.filter(u=>u.includes('/neighbors')).at(-1);for(const part of ['label=car','review_status=pending','collection_id='+collection])if(!lastNeighbors.includes(part))throw Error('Unfiltered next-image queue');
  await page.getByRole('spinbutton',{name:'跳转图片序号',exact:true}).fill('1');await page.getByRole('spinbutton',{name:'跳转图片序号',exact:true}).press('Enter');await page.waitForURL('**/annotate/'+a+'?**');
  if(!reads.some(u=>u.includes('/assets?')&&u.includes('page=1')&&u.includes('page_size=1')&&u.includes('review_status=pending')))throw Error('Jump did not use filtered queue');
  await page.locator('#image-annotation-description').fill('未保存修改');
  if(!await page.getByRole('combobox',{name:'筛选标注标签',exact:true}).isDisabled())throw Error('Dirty draft allows filter');
  await page.locator('#image-annotation-description').fill('');
  const old=page.url();await select('筛选审核状态','已驳回');await page.getByText('当前筛选没有匹配图片，已保留当前图片和筛选',{exact:true}).waitFor();if(page.url()!==old)throw Error('Empty filter lost current queue');
  await page.getByRole('link',{name:'下一张',exact:true}).click();await page.waitForURL('**/annotate/'+b+'?**');
  await page.getByRole('button',{name:'审核通过',exact:true}).click();
  await page.getByText('当前图片已不符合筛选，上一张 / 下一张仍在筛选结果中切换',{exact:true}).waitFor();
  await page.getByRole('button',{name:'清除筛选',exact:true}).click();await page.waitForURL(url=>!url.searchParams.has('label')&&!url.searchParams.has('review_status')&&!url.searchParams.has('collection_id'));
  await page.getByText('第 2 / 2 张',{exact:true}).waitFor();
  failNeighbors=true;await page.reload();await page.getByRole('button',{name:'重试切图读取',exact:true}).waitFor();failNeighbors=false;await page.getByRole('button',{name:'重试切图读取',exact:true}).click();await page.getByText('第 2 / 2 张',{exact:true}).waitFor();
  await page.screenshot({path:'output/playwright/image-workspace-filters-desktop.png'});
  if(errors.length)throw Error(JSON.stringify(errors));
  return {passed:true,combined_filters:true,filtered_next_and_jump:true,dirty_guard:true,empty_filter_preserves_queue:true,review_refreshes_queue:true,clear_filters:true,navigation_failure_retry:true,errors};
}
