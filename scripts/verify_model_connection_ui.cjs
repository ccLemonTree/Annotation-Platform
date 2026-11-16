async page => {
  const errors=[], writes=[];
  page.setDefaultTimeout(12000);
  const onError=error=>errors.push(error.message);
  page.on('pageerror',onError);
  const base={kind:'detection',protocol:'model_service',base_url:'http://fixture.test',endpoint:'/detect/test',model_name:'',enabled:true,
    timeout_seconds:5,confidence:.25,iou:.45,labels:[],label_mapping:{},api_key_env:'',revision:1};
  let models=[{...base,id:'11111111-1111-4111-8111-111111111111',key:'online',name:'在线测试模型'},
    {...base,id:'22222222-2222-4222-8222-222222222222',key:'offline',name:'离线测试模型'}];
  const pattern=/^http:\/\/(localhost|127\.0\.0\.1):3000\/api\//;
  await page.addInitScript(()=>{if(location.protocol.startsWith('http'))localStorage.setItem('token','isolated-model-state-token');});
  await page.route(pattern,async route=>{
    const request=route.request(),path=new URL(request.url()).pathname,method=request.method();
    if(path==='/api/auth/me')return route.fulfill({json:{id:1,username:'fixture',role:'admin',tenant_id:1,is_active:true}});
    if(path==='/api/model-services/prompt-defaults')return route.fulfill({json:{caption:'描述',review:'审核',scene:'场景'}});
    if(path==='/api/model-services'&&method==='GET')return route.fulfill({json:models});
    const model=models.find(model=>path.includes(model.id));
    if(model&&path.endsWith('/test')){
      const online=model.key==='online';
      model.connection_check={status:online?'online':'offline',detail:online?'连接成功':'模型服务超时，请稍后重试',
        checked_at:'2026-11-02T12:00:00+00:00',checked_revision:model.revision};
      return route.fulfill({status:online?200:504,json:online?{ok:true,detail:'连接成功'}:{detail:model.connection_check.detail}});
    }
    if(model&&method==='PUT'){
      const body=request.postDataJSON();writes.push(body);
      if('connection_check'in body||'revision'in body||'id'in body)throw Error('只读连接结果混入模型配置');
      models=models.map(value=>value.id===model.id?{...value,...body,revision:value.revision+1,connection_check:null}:value);
      return route.fulfill({json:models.find(value=>value.id===model.id)});
    }
    throw Error('Unexpected fixture API '+method+' '+path);
  });
  try{
    await page.setViewportSize({width:1440,height:1000});
    await page.goto('http://127.0.0.1:3000/models');
    let offline=page.getByRole('row').filter({hasText:'离线测试模型'});
    await offline.getByText('离线',{exact:true}).waitFor();
    await offline.getByText('已启用',{exact:true}).waitFor();
    await offline.getByText('模型服务超时，请稍后重试',{exact:true}).waitFor();
    await page.getByRole('row').filter({hasText:'在线测试模型'}).getByText('在线',{exact:true}).waitFor();
    await offline.getByRole('button',{name:'停用',exact:true}).click();
    await offline.getByText('已停用',{exact:true}).waitFor();
    await offline.getByText('离线',{exact:true}).waitFor();
    await page.getByRole('button',{name:'刷新',exact:true}).click();
    await offline.getByText('离线',{exact:true}).waitFor();
    if(writes.length!==1)throw Error('Unexpected configuration writes');
    if(errors.length)throw Error(errors.join(';'));
    await page.screenshot({path:'output/playwright/model-connection-state.png'});
    return {checks:['打开列表自动检测','启用和在线分开','离线原因可见且刷新保留','启停不提交只读字段'],writes:writes.length};
  }finally{
    await page.goto('about:blank');await page.unroute(pattern);page.off('pageerror',onError);
  }
}
