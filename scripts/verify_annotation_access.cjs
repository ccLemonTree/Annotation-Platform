async page => {
  await page.unrouteAll();page.setDefaultTimeout(10000);
  const checks=[],errors=[],writes=[],calls=[];
  page.on('pageerror',error=>errors.push(error.message));
  const ensure=(value,label)=>{if(!value)throw Error(label);checks.push(label);};
  const pid='11111111-1111-4111-8111-111111111111',aid='22222222-2222-4222-8222-222222222222';
  let role='user',taskType='detection',authFails=false;
  const project=()=>({id:pid,project_name:'权限验收项目',task_type:taskType,label_schema:{labels:['person']},total_assets:1,annotated_count:0,reviewed_count:0});
  const asset={id:aid,project_id:pid,original_name:'权限验收.png',source_type:'upload',width:1,height:1};
  const png=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aC+sAAAAASUVORK5CYII=','base64');
  await page.route(/^http:\/\/localhost:3000\/api\//,route=>{
    const req=route.request(),path=new URL(req.url()).pathname;calls.push(path);
    const json=(body,status=200)=>route.fulfill({json:body,status});
    if(path==='/api/auth/me')return json(authFails?{detail:'隔离账户读取失败'}:{id:1,tenant_id:1,username:'权限验收',role,is_active:true},authFails?503:200);
    if(path==='/api/auth/login')return json({access_token:'isolated-access',token_type:'bearer'});
    if(req.method()!=='GET'){
      writes.push({path,data:req.postDataJSON()});
      if(path==='/api/annotations')return json({...req.postDataJSON(),id:'annotation',version:1,is_latest:true,review_status:'pending'},201);
      return json({});
    }
    if(path==='/api/projects')return json({items:[project()],total:1});
    if(path==='/api/projects/'+pid)return json(project());
    if(path.endsWith('/stats'))return json(project());
    if(path==='/api/assets')return json({items:[asset],total:1});
    if(path==='/api/assets/'+aid)return json(asset);
    if(path.endsWith('/neighbors'))return json({previous_id:null,next_id:null,position:1,total:1});
    if(path.endsWith('/content'))return route.fulfill({contentType:'image/png',body:png});
    if(path==='/api/annotations')return json({items:[],total:0});
    if(path==='/api/model-services'||path.endsWith('/events')||path.endsWith('/matches')||path.endsWith('/work/jobs')||path.endsWith('/smart-search/platforms'))return json([]);
    return json({items:[],total:0});
  });
  const open=path=>page.goto('http://localhost:3000'+path);
  await open('/login');await page.evaluate(()=>{localStorage.clear();localStorage.setItem('token','isolated-access');localStorage.setItem('user',JSON.stringify({role:'admin',username:'伪造缓存'}));});
  await page.setViewportSize({width:1440,height:1000});
  await open('/');await page.waitForURL('**/annotations');await page.getByRole('region',{name:'标注项目 权限验收项目',exact:true}).waitFor();
  ensure((await page.getByRole('navigation',{name:'平台主导航',exact:true}).getByRole('menuitem').allTextContents()).join('|')==='标注与审核','non-admin sees only annotation navigation, even with cached admin role');
  const allowedLinks=async()=>ensure((await page.locator('main a[href],.platform-content a[href]').evaluateAll(nodes=>nodes.map(node=>node.getAttribute('href')))).every(href=>/^\/(annotations(?:\?|$)|annotate\/)/.test(href)),'annotation content links stay within annotation pages');
  await allowedLinks();
  await page.getByRole('combobox',{name:'搜索平台功能',exact:true}).click();
  ensure(!await page.locator('.ant-select-dropdown:visible').getByText(/项目管理|模型管理|导出数据|训练/).count(),'feature search hides management shortcuts');await page.keyboard.press('Escape');
  for(const path of ['/projects','/assets','/models','/model-preview','/automation','/analytics','/validation','/training','/model-deployments','/storage-maintenance','/gpu-resources','/gpu-terminal','/smarteye','/detection-query','/image-semantics/history',`/projects/${pid}/images`,`/projects/${pid}/prepare`,`/projects/${pid}/deliver`,'/unknown']){
    await open(path);await page.waitForURL('**/annotations');
  }
  ensure(!calls.some(path=>/^\/api\/(training|gpu-|model-deployments|validation)/.test(path)),'blocked pages never mount or request their APIs');
  await open('/annotations?project_id='+pid);await page.getByRole('navigation',{name:'项目导航',exact:true}).waitFor();await allowedLinks();
  await page.getByRole('navigation',{name:'项目导航',exact:true}).getByRole('button',{name:/^(开始|继续)标注$/}).click();await page.waitForURL(url=>url.pathname==='/annotate/'+aid);await page.getByRole('img',{name:'检测画框区域',exact:true}).waitFor();await allowedLinks();
  for(const type of ['classification','caption']){
    taskType=type;await open('/annotate/'+aid);await page.locator('#image-annotation-description').waitFor();await allowedLinks();
  }
  await page.getByLabel('图片描述',{exact:true}).fill('普通用户可以继续人工标注');await page.keyboard.press('Control+s');
  await page.waitForFunction(()=>document.body.textContent.includes('v1'));
  ensure(writes.some(row=>row.path==='/api/annotations'&&row.data.annotation_data.description==='普通用户可以继续人工标注'),'non-admin can save image annotations');
  taskType='vector';await open('/annotate/project/'+pid);await page.getByText('图片描述标注',{exact:true}).first().waitFor();await allowedLinks();
  await page.setViewportSize({width:390,height:844});await open('/annotations');await page.getByRole('button',{name:'展开导航',exact:true}).click();
  const mobile=page.getByRole('navigation',{name:'平台主导航',exact:true});await mobile.waitFor();
  ensure((await mobile.getByRole('menuitem').allTextContents()).join('|')==='标注与审核','mobile navigation only shows annotations');
  role='annotator';await open('/training');await page.waitForURL('**/annotations');checks.push('annotator role also redirects to annotations');
  role='admin';await page.setViewportSize({width:1440,height:1000});await open('/projects');await page.getByRole('heading',{name:'项目管理',exact:true}).waitFor();
  ensure(await page.getByRole('navigation',{name:'平台主导航',exact:true}).getByRole('menuitem').count()>10,'admin keeps full navigation');
  await open('/annotations?project_id='+pid);await page.getByRole('link',{name:'5 导出与训练',exact:true}).waitFor();checks.push('admin keeps project management workflow');
  role='user';authFails=true;await open('/training');await page.getByRole('alert').filter({hasText:'读取账户信息失败'}).waitFor();
  ensure(!await page.locator('.platform-shell').count(),'account lookup failure does not render protected pages');authFails=false;
  await page.evaluate(()=>localStorage.clear());await open('/training');await page.waitForURL('**/login');checks.push('unauthenticated access redirects to login');
  await page.getByPlaceholder('用户名',{exact:true}).fill('isolated');await page.getByPlaceholder('密码',{exact:true}).fill('isolated');await page.getByRole('button',{name:/登\s*录/}).click();await page.waitForURL('**/annotations');checks.push('non-admin login lands on annotations');
  await page.getByRole('region',{name:'标注项目 权限验收项目',exact:true}).waitFor();
  ensure(errors.length===0,'no browser page errors: '+errors.join(';'));
  await page.screenshot({path:'output/playwright/annotation-access-user.png'});
  return {checks,scope:'isolated API fixtures only; no business data or remote writes'};
}
