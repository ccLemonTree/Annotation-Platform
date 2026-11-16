async page=>{
  page.setDefaultTimeout(12000);
  const project={id:'11111111-1111-4111-8111-111111111111',project_name:'忙碌隔离项目',task_type:'detection',status:'active',created_at:'2026-11-02T00:00:00Z',label_schema:{labels:['人']},total_assets:1,annotated_count:0,reviewed_count:0};
  const base={enabled:true,key:'fixture',base_url:'http://fixture.invalid',endpoint:'/detect',model_name:'fixture',timeout_seconds:30,confidence:.3,iou:.5,labels:['人'],label_mapping:{},revision:1};
  const models=[{...base,id:'detector',name:'隔离检测',kind:'detection',protocol:'model_service'},{...base,id:'reviewer',name:'隔离审核',kind:'caption',protocol:'openai_chat'}];
  const empty={items:[],total:0,page:1,page_size:20},writes=[];
  let release;const gate=new Promise(resolve=>{release=resolve;});
  await page.addInitScript(()=>{if(location.protocol==='http:')localStorage.setItem('token','isolated-busy-token');});
  await page.route('**/api/**',async route=>{
    const request=route.request(),path=new URL(request.url()).pathname;
    if(!path.startsWith('/api/'))return route.continue();
    if(path==='/api/auth/me')return route.fulfill({json:{id:1,username:'fixture',role:'admin',tenant_id:1,is_active:true}});
    if(path==='/api/projects')return route.fulfill({json:{items:[project],total:1,page:1,page_size:10}});
    if(path==='/api/model-services')return route.fulfill({json:models});
    if(path==='/api/automation/jobs'&&request.method()==='POST'){
      writes.push(request.postDataJSON());await gate;
      return route.fulfill({json:{...writes[0],id:'isolated-created',status:'pending',total:1,processed:0,approved_count:0,needs_review_count:0,failed_count:0,created_at:'2026-11-02T00:00:00Z'}});
    }
    if(path==='/api/automation/jobs'||path==='/api/automation/review-queue')return route.fulfill({json:empty});
    throw Error('Unmocked API: '+request.method()+' '+path);
  });
  try{
    await page.goto('http://127.0.0.1:3000/projects');
    await page.getByRole('button',{name:'预标注与AI审核',exact:true}).click();
    const drawer=page.getByRole('dialog').filter({has:page.getByRole('button',{name:'创建任务',exact:true})});
    await drawer.getByRole('button',{name:'创建任务',exact:true}).click();
    const form=page.getByRole('dialog',{name:'创建预标注 / AI审核任务',exact:true});
    const posted=page.waitForRequest(request=>new URL(request.url()).pathname==='/api/automation/jobs'&&request.method()==='POST');
    await form.getByRole('button',{name:'提交任务',exact:true}).click();await posted;
    if(!await form.getByRole('button',{name:'关闭创建',exact:true}).isDisabled())throw Error('Busy form close remained enabled');
    await page.keyboard.press('Escape');await page.keyboard.press('Escape');
    const afterEscape={form:await form.isVisible(),drawer:await drawer.isVisible()};
    let drawerCloseClickable=true;
    try{await drawer.locator('.ant-drawer-close').click({trial:true,timeout:700});}catch{drawerCloseClickable=false;}
    const bounds=await form.boundingBox();
    await page.mouse.click(Math.max(4,bounds.x-20),Math.max(4,bounds.y+30));
    const afterMask={form:await form.isVisible(),drawer:await drawer.isVisible()};
    if(!afterEscape.form||!afterEscape.drawer||!afterMask.form||!afterMask.drawer||drawerCloseClickable||writes.length!==1)throw Error('Busy modal guard failed '+JSON.stringify({afterEscape,afterMask,drawerCloseClickable,writes:writes.length}));
    release();await form.waitFor({state:'hidden'});
    return {checks:['Two Escape presses retain busy form and drawer','Modal mask prevents drawer close clicks','Mask click retains busy form','One create request stays bound to original project'],afterEscape,afterMask,drawerCloseClickable,createRequests:writes.length,scope:'Separate browser session; all API traffic intercepted; no real task created'};
  }finally{release();await page.goto('about:blank');await page.unroute('**/api/**');}
}
