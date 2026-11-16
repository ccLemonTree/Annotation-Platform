async page=>{
  page.setDefaultTimeout(12000);
  const base='http://localhost:3000',projectId='11111111-1111-4111-8111-111111111111';
  const returnPath='/smarteye?project_id='+projectId;
  const pattern=/^https?:\/\/[^/]+\/api\//,requests=[];
  let role='admin',expired='axios';
  const project={id:projectId,project_name:'隔离鉴权项目',task_type:'detection',label_schema:{labels:['人']},status:'active'};
  await page.route(pattern,async route=>{
    const request=route.request(),url=new URL(request.url()),child=request.frame()!==page.mainFrame();
    requests.push({path:url.pathname,method:request.method(),child,hasAuthorization:!!request.headers().authorization});
    if(url.origin!==base)return route.fulfill({status:401,headers:{'Access-Control-Allow-Origin':base},json:{detail:'foreign401'}});
    let data;
    if(url.pathname==='/api/auth/login'){
      const values=new URLSearchParams(request.postData()||'');
      if(values.get('password')==='wrong')return route.fulfill({status:401,json:{detail:'隔离测试：账号或密码错误'}});
      data={access_token:'valid-fixture',token_type:'bearer',expires_in:3600};
    }else if(child&&expired&&((expired==='axios'&&url.pathname==='/api/images/collections')||(expired==='fetch'&&url.pathname==='/api/smarteye/tasks'))){
      expired='';return route.fulfill({status:401,json:{detail:'隔离测试：登录过期'}});
    }else if(url.pathname==='/api/auth/me')data={id:1,username:'fixture',tenant_id:1,role,is_active:true};
    else if(url.pathname==='/api/projects')data={items:[project],total:1,page:1,page_size:100};
    else if(url.pathname==='/api/projects/'+projectId)data=project;
    else if(url.pathname==='/api/images/ingest-settings')data={classification_enabled:false,scenes:['工厂'],min_long_edge:0};
    else if(url.pathname.startsWith('/api/smarteye/'))data={code:0,data:[]};
    else if(url.pathname==='/api/annotations')data={items:[],total:0,page:1,page_size:20};
    else if(url.pathname==='/api/annotations/stats')data={total:0,pending:0,approved:0,rejected:0};
    else data=[];
    await route.fulfill({json:data});
  });
  const assertTopLogin=async()=>{
    await page.waitForURL(url=>url.pathname==='/login');
    await page.getByRole('heading',{name:'登录工作台'}).waitFor();
    if(page.frames().length!==1)throw Error('登录仍发生在iframe内，可能再次出现双导航');
    if(new URL(page.url()).searchParams.get('next')!==returnPath)throw Error('登录丢失顶层项目返回路径');
  };
  const signIn=async password=>{
    await page.getByPlaceholder('用户名',{exact:true}).fill('fixture');
    await page.getByPlaceholder('密码',{exact:true}).fill(password);
    await page.locator('button[type="submit"]').first().click();
  };
  const assertReturned=async()=>{
    await page.waitForURL(url=>url.pathname==='/smarteye'&&url.searchParams.get('project_id')===projectId);
    await page.locator('iframe[title="SmartEye 数据拉取"]').waitFor();
    const frame=await page.locator('iframe[title="SmartEye 数据拉取"]').elementHandle().then(element=>element.contentFrame());
    await frame.waitForURL(url=>url.pathname==='/smarteye/index.html'&&url.searchParams.get('project_id')===projectId);
    await frame.waitForLoadState('domcontentloaded');
    if(new URL(frame.url()).searchParams.get('project_id')!==projectId)throw Error('重新登录后iframe丢失项目上下文');
    if(await frame.locator('.platform-sidebar').count())throw Error('iframe中再次嵌入主系统导航');
    return frame;
  };
  try{
    await page.goto(base+'/login');
    await page.evaluate(()=>{localStorage.setItem('auth-fixture-started','1');localStorage.setItem('token','expired-fixture');localStorage.removeItem('user');});
    await page.goto(base+returnPath);
    await assertTopLogin();
    const loginUrl=page.url();
    await signIn('wrong');
    await page.getByRole('alert').filter({hasText:'隔离测试：账号或密码错误'}).waitFor();
    if(page.url()!==loginUrl||await page.getByPlaceholder('用户名',{exact:true}).inputValue()!=='fixture')throw Error('密码错误401触发整页重定向，登录错误或表单丢失');
    await signIn('correct');
    let frame=await assertReturned();
    await frame.evaluate(async()=>{const auth=await import('/src/api/auth.ts');auth.logout();}).catch(()=>{});
    await assertTopLogin();await signIn('correct');frame=await assertReturned();
    const rejected=await page.evaluate(async()=>{
      const {safeReturnPath}=await import('/src/utils/authNavigation.ts');
      return ['//outside.invalid','https://outside.invalid','/\\outside','/%5coutside','/smarteye%0aLocation:x','/login?next=/smarteye','/a/../login','/%2foutside','/a/..//outside','/smarteye%85'].map(path=>safeReturnPath(path));
    });
    if(rejected.some(path=>path!=='/'))throw Error('不安全next或login循环未被拒绝');
    const beforeForeign=page.url();
    await frame.evaluate(async()=>{const network=await import('/smarteye/network.ts');await network.apiFetch('https://auth-fixture.invalid/api/ping');});
    if(page.url()!==beforeForeign||await page.evaluate(()=>localStorage.getItem('token'))!=='valid-fixture'||requests.find(request=>request.path==='/api/ping')?.hasAuthorization)throw Error('外站401错误触发登录清理或携带平台token');
    expired='fetch';
    await frame.evaluate(async()=>{const network=await import('/smarteye/network.ts');await network.apiFetch('/api/smarteye/tasks');}).catch(()=>{});
    await assertTopLogin();role='annotator';await signIn('correct');
    await page.waitForURL(url=>url.pathname==='/annotations');
    if(await page.locator('iframe').count()||await page.getByRole('menuitem',{name:'数据拉取',exact:true}).count())throw Error('普通用户登录后越过项目权限');
    await page.screenshot({path:'output/playwright/smarteye-auth-login.png',fullPage:true,animations:'disabled'});
    role='admin';await page.evaluate(()=>localStorage.removeItem('token'));await page.goto(base+returnPath);await assertTopLogin();
    return {checks:['iframe axios401转顶层登录','保留project_id安全next','密码错误401保留表单错误','管理员返回数据拉取','iframe退出登录转顶层','fetch401转顶层','拒绝外站反斜杠控制字符与登录循环','外站401不清登录且不携带token','普通用户固定标注入口','无token保留项目返回路径'],scope:'全部/api拦截，登录/用户/SmartEye均替代响应，无真实鉴权或业务写入',requests};
  }catch(error){await page.screenshot({path:'output/playwright/smarteye-auth-failure.png',fullPage:true});throw error;}
  finally{await page.goto('about:blank');await page.unroute(pattern);}
}
