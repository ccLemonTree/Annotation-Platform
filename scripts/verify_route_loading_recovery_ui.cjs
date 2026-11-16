async page=>{
  const base='http://127.0.0.1:3000',checks=[],unexpected=[];let failModule=true;
  await page.addInitScript(()=>localStorage.setItem('token','isolated-route-loading'));
  await page.route(/^https?:\/\/[^/]+\/api(?:\/|\?|$)/,route=>{
    const path=new URL(route.request().url()).pathname;
    if(path==='/api/auth/me')return route.fulfill({json:{id:1,username:'fixture',role:'admin',tenant_id:1}});
    if(path==='/api/projects')return route.fulfill({json:{items:[],total:0,page:1,page_size:10}});
    unexpected.push(path);return route.abort();
  });
  await page.route('**/src/pages/StorageMaintenancePage.tsx*',route=>failModule?route.abort():route.continue());
  await page.goto(base+'/storage-maintenance');await page.getByText('页面加载失败',{exact:true}).waitFor();
  if(!await page.getByRole('button',{name:'重新加载页面',exact:true}).isVisible())throw Error('Missing retry');
  checks.push('懒加载模块失败显示错误和重试，不暴露内部异常堆栈');
  if(await page.getByText(/Unexpected Application Error|Failed to fetch dynamically imported module/).count())throw Error('Technical error leaked');
  failModule=false;await page.getByRole('button',{name:'重新加载页面',exact:true}).click();
  await page.getByRole('button',{name:'扫描预览',exact:true}).waitFor();checks.push('模块恢复后重新加载可打开原页面');
  failModule=true;await page.goto(base+'/storage-maintenance');await page.getByText('页面加载失败',{exact:true}).waitFor();
  await page.getByRole('link',{name:'返回项目列表',exact:true}).click();
  await page.getByRole('button',{name:'创建第一个项目',exact:true}).waitFor();checks.push('模块加载失败后可返回项目列表继续操作');
  if(unexpected.length)throw Error('Unexpected API '+unexpected.join(','));
  return {passed:true,checks,unexpected,expected_module_failure:true};
}
