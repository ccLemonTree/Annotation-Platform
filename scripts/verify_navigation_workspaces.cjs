async page => {
  await page.unrouteAll();page.setDefaultTimeout(8000);
  const errors=[],writes=[];page.on('pageerror',error=>errors.push(error.message));
  const pid='33333333-3333-4333-8333-333333333333',aid='55555555-5555-4555-8555-555555555555';
  const project={id:pid,project_name:'检测导航验收',task_type:'detection',label_schema:{labels:['person']},total_assets:1,annotated_count:0,reviewed_count:0};
  const servers=[{id:'default',name:'默认服务器',enabled:true,source:'ssh_manager'},{id:'gpu-2',name:'第二服务器',enabled:true,source:'ssh_manager'}];
  await page.route(/^http:\/\/localhost:3000\/api\//,route=>{
    const request=route.request(),path=new URL(request.url()).pathname;
    const json=(body,status=200)=>route.fulfill({json:body,status});
    if(request.method()!=='GET'){writes.push(path);return route.abort();}
    if(path==='/api/auth/me')return json({id:1,tenant_id:1,role:'admin',username:'验收'});
    if(path==='/api/projects/'+pid)return json(project);
    if(path.startsWith('/api/projects/'))return json({detail:'不存在'},404);
    if(path==='/api/assets/'+aid)return json({id:aid,project_id:pid,original_name:'person.png',source_type:'upload',width:640,height:480});
    if(path.endsWith('/neighbors'))return json({previous_id:null,next_id:null,position:1,total:1});
    if(path.endsWith('/content'))return route.fulfill({contentType:'image/png',body:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aC+sAAAAASUVORK5CYII=','base64')});
    if(path==='/api/annotations')return json({items:[],total:0});
    if(path.endsWith('/events'))return json([]);
    if(path==='/api/gpu-resources/servers')return json(servers);
    if(path.endsWith('/release/tasks'))return json([]);
    if(path.includes('/resources')||path==='/api/gpu-resources')return json({server_name:'第二服务器',gpus:[],processes:[],warnings:[],processes_complete:true,collected_at:new Date().toISOString()});
    return json({items:[],total:0});
  });
  await page.goto('http://localhost:3000/login');await page.evaluate(()=>{localStorage.setItem('token','isolated');localStorage.setItem('user',JSON.stringify({role:'admin',username:'验收'}));});
  await page.setViewportSize({width:1440,height:1000});await page.goto('http://localhost:3000/annotate/'+aid);
  await page.locator('.workspace-canvas').waitFor();
  const bounds=await page.locator('.workspace-canvas').boundingBox();
  if(!bounds||bounds.height<160||bounds.y+bounds.height>1000)throw Error('项目导航挤出检测画布 '+JSON.stringify(bounds));
  await page.screenshot({path:'output/playwright/navigation-detection.png',fullPage:true});
  await page.goto('http://localhost:3000/assets?project_id='+pid);
  await page.getByRole('navigation',{name:'项目导航',exact:true}).waitFor();
  if(!page.url().endsWith('/projects/'+pid+'/images'))throw Error('旧图片入口不兼容');
  await page.goto('http://localhost:3000/projects/not-found/prepare');
  await page.getByText('读取项目失败，请重试或返回项目列表',{exact:true}).waitFor();
  await page.getByRole('link',{name:'全部项目',exact:true}).waitFor();
  await page.goto('http://localhost:3000/gpu-terminal?server_id=gpu-2');
  await page.getByRole('link',{name:'查看此服务器GPU',exact:true}).click();
  await page.getByRole('combobox',{name:'GPU服务器',exact:true}).waitFor();
  if(!page.url().includes('server_id=gpu-2'))throw Error('丢失服务器参数');
  const selected=await page.getByRole('combobox',{name:'GPU服务器',exact:true}).locator('xpath=ancestor::div[contains(@class,"ant-select")][1]').innerText();
  if(!selected.includes('第二服务器'))throw Error('GPU页未恢复第二服务器');
  if(errors.length||writes.length)throw Error(JSON.stringify({errors,writes}));
  return {checks:['检测画布保留可用高度','旧项目图片地址兼容','项目读取错误可返回','SSH→GPU保留对应服务器'],pageErrors:errors,realBusinessWrites:0};
}
