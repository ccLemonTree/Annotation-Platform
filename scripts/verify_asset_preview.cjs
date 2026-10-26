async (page) => {
  const assert = (value, reason) => { if (!value) throw Error(reason); };
  page.setDefaultTimeout(5000);
  const lists = [], writes = [], checks = [];
  let holdPage = false, releasePage, holdContent = false, releaseContent, failPage = false, failContent = false;
  const svg = '<svg xmlns="http://www.w3.org/2000/svg" width="64" height="48"><rect width="64" height="48" fill="blue"/></svg>';
  await page.route('**/api/**', async route => {
    const request = route.request(), url = new URL(request.url());
    if (!url.pathname.startsWith('/api/')) return route.continue();
    if (!['GET', 'HEAD', 'OPTIONS'].includes(request.method())) {
      writes.push(request.url()); return route.abort();
    }
    if (/\/content$/.test(url.pathname)) {
      if (failContent && /\/item-21\//.test(url.pathname)) return route.fulfill({status:503,json:{detail:'test content failure'}});
      if (holdContent && /\/item-21\//.test(url.pathname)) await new Promise(resolve => { releaseContent = resolve; });
      return route.fulfill({contentType:'image/svg+xml',body:svg}).catch(()=>{});
    }
    if (['/api/images','/api/assets'].includes(url.pathname)) {
      const p = Number(url.searchParams.get('page') || 1), size = Number(url.searchParams.get('page_size') || 20);
      lists.push({path:url.pathname, params:Object.fromEntries(url.searchParams)});
      if (failPage && p===2) return route.fulfill({status:503,json:{detail:'test page failure'}});
      if (holdPage && p === 2) await new Promise(resolve => { releasePage = resolve; });
      return route.fulfill({json:{total:43,page:p,page_size:size,items:Array.from({length:Math.min(size,43-(p-1)*size)},(_,i)=>({
        id:`item-${(p-1)*size+i+1}`,original_name:`image-${(p-1)*size+i+1}.svg`,project_id:'preview-project',
        width:64,height:48,file_size:100,source_type:'upload',created_at:'2026-10-15T00:00:00Z',
        annotation_status:'pending',scene_status:'completed',scene_labels:[],collection_names:['预览测试'],
      }))}}).catch(()=>{});
    }
    if (url.pathname === '/api/images/collections') return route.fulfill({json:[{id:'preview-collection',name:'预览测试',image_count:43}]});
    if (url.pathname === '/api/projects') return route.fulfill({json:{items:[],total:0}});
    if (url.pathname === '/api/projects/preview-project') return route.fulfill({json:{id:'preview-project',project_name:'预览项目'}});
    return route.fulfill({json:[]});
  });
  await page.addInitScript(()=>{localStorage.setItem('token','preview-fixture');localStorage.setItem('user',JSON.stringify({username:'preview',role:'admin'}));});
  const preview = page.locator('.ant-image-preview');
  const position = async n => { await preview.getByText(`${n} / 43`,{exact:true}).waitFor(); };
  const close = async()=>{await page.keyboard.press('Escape'); await preview.waitFor({state:'hidden'});};
  try {
    for (const path of ['/assets','/projects/preview-project/images']) {
      await page.goto(`http://localhost:3000${path}`);
      await page.locator('td[title="image-1.svg"]').waitFor();
      if (path === '/assets') {
        await page.getByPlaceholder('按文件名搜索').fill('image-');
        await page.getByPlaceholder('按文件名搜索').press('Enter');
        await page.locator('td[title="image-1.svg"]').waitFor();
      }
      await page.locator('tbody .ant-image').first().click();
      await position(1);
      assert(await preview.getByRole('button',{name:'上一张',exact:true}).isDisabled(),'First boundary must be disabled');
      for(const key of ['Shift+ArrowRight','Control+ArrowRight','Alt+ArrowRight','Meta+ArrowRight']) await page.keyboard.press(key);
      await position(1);
      await preview.evaluate(el=>{const input=document.createElement('input');input.setAttribute('data-preview-test','');el.append(input);input.focus();});
      await page.keyboard.press('ArrowRight'); await position(1);
      await preview.evaluate(el=>{el.querySelector('[data-preview-test]').remove();el.focus();});
      await page.evaluate(()=>{const el=document.createElement('div');el.setAttribute('role','dialog');el.setAttribute('aria-modal','true');el.setAttribute('data-preview-test','');el.textContent='Other dialog';document.body.append(el);});
      await page.keyboard.press('ArrowRight'); await position(1);
      await page.locator('[data-preview-test]').evaluate(el=>el.remove());
      await page.keyboard.press('ArrowRight'); await position(2);
      await page.keyboard.press('ArrowLeft'); await position(1);
      for(let n=2;n<=21;n++){await page.keyboard.press('ArrowRight');await position(n);}
      assert(lists.some(call=>call.params.page==='2'),'Must fetch page two');
      for(let n=22;n<=43;n++){await preview.getByRole('button',{name:'下一张',exact:true}).click();await position(n);}
      assert(await preview.getByRole('button',{name:'下一张',exact:true}).isDisabled(),'Last boundary must be disabled');
      await page.keyboard.press('ArrowRight'); await position(43);
      for(let n=42;n>=20;n--){await page.keyboard.press('ArrowLeft');await position(n);}
      await close();
      const count=lists.length; await page.keyboard.press('ArrowRight');
      assert(lists.length===count,'Closed preview must not navigate');
      assert(await page.locator('td[title="image-1.svg"]').count()===1,'Preview must preserve table page');
      checks.push(`${path}: keys/buttons, both page boundaries, first/last, modifiers, close`);
    }
    assert(lists.every(call=>Number(call.params.page_size)===20),'No full-library fetch');
    assert(lists.filter(call=>call.path==='/api/assets').every(call=>call.params.project_id==='preview-project'),'Project scope lost');
    assert(lists.filter(call=>call.path==='/api/images'&&Number(call.params.page)>1).every(call=>call.params.q==='image-'),'Cross-page filter lost');
    await page.goto('http://localhost:3000/assets');
    await page.locator('tbody .ant-image').last().click(); await position(20);
    failPage=true; await page.keyboard.press('ArrowRight');
    await preview.getByRole('alert').waitFor(); await position(20);
    failPage=false; failContent=true; await page.keyboard.press('ArrowRight'); await position(21);
    await preview.getByText('图片加载失败，可切换其他图片',{exact:true}).waitFor();
    failContent=false; await page.keyboard.press('ArrowRight'); await position(22);
    await preview.locator('img').waitFor();
    assert(await preview.locator('img').evaluate(el=>el.naturalWidth===64),'Preview must decode image');
    await close(); checks.push('page failure retries; content failure keeps navigation and close usable');
    await page.locator('tbody .ant-image').last().click(); await position(20);
    holdPage=true; await page.keyboard.press('ArrowRight');
    await page.waitForFunction(()=>document.querySelector('button[aria-label="下一张"]')?.disabled);
    const canceledPage=page.waitForEvent('requestfailed',request=>new URL(request.url()).pathname==='/api/images'&&new URL(request.url()).searchParams.get('page')==='2');
    await close(); await canceledPage; releasePage(); holdPage=false;
    await page.locator('tbody .ant-image').first().click(); await position(1);
    await close(); checks.push('close during pending page request; reopen retains new selection');
    holdContent=true;
    await page.locator('tbody .ant-image').last().click(); await position(20);
    await page.keyboard.press('ArrowRight'); await position(21);
    await page.waitForFunction(()=>document.body.innerText.includes('图片加载中'));
    const canceledContent=page.waitForEvent('requestfailed',request=>new URL(request.url()).pathname==='/api/images/item-21/content');
    await close(); await canceledContent; releaseContent(); holdContent=false;
    await page.locator('tbody .ant-image').first().click(); await position(1);
    await close(); checks.push('close during pending content request');
    assert(writes.length===0,`Unexpected write attempts: ${writes}`);
    return {checks,listRequests:lists.length,maxPageSize:20,writes:writes.length};
  } finally {
    releasePage?.(); releaseContent?.();
    await page.unroute('**/api/**');
  }
}
