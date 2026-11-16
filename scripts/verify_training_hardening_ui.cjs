async page=>{
  page.setDefaultTimeout(12000);
  const projectId='11111111-1111-4111-8111-111111111111',snapshotId='33333333-3333-4333-8333-333333333333';
  const idA='44444444-4444-4444-8444-444444444444',idB='55555555-5555-4555-8555-555555555555';
  const checks=[],requests=[],writes=[],unexpected=[],errors=[];
  const user={id:1,username:'hardening-admin',role:'admin',tenant_id:1,is_active:true};
  const project={id:projectId,project_name:'训练健壮性隔离回归',task_type:'detection',status:'active',label_schema:{labels:['car']},total_assets:2,annotated_count:2,reviewed_count:2};
  const snapshot={id:snapshotId,format:'yolo',content_hash:'a'.repeat(64),created_at:'2026-11-06T00:00:00Z',manifest:{labels:['car'],approved_only:true,assets:[{split:'train',review_status:'approved'},{split:'val',review_status:'approved'}]}};
  const makeRun=(id,name,config)=>({id,name,kind:'yolo26',status:'completed',server:{id:'default',name:'隔离服务器'},source:{kind:'snapshot',id:snapshotId,project_id:projectId,sha256:'a'.repeat(64)},output_dir:'/fixture/runs/'+id,created_at:'2026-11-06T00:00:00Z',inspection:{},
    config:config||{kind:'yolo26',execution_backend:'docker',gpu_uuids:['GPU-fixture-1'],server_id:'default',model_path:'/fixture/model.pt',dataset_path:'',snapshot_id:snapshotId,image:'fixture/yolo',output_root:'/fixture/runs',output_subdir:'train',shm_size_gb:16,parameters:{epochs:2,batch:4,device:'auto'},environment:{},mounts:[]}});
  const runs=[makeRun(idA,'任务A'),makeRun(idB,'任务B')],gates=new Map(),failNext=new Set();
  const gate=key=>{let release;const promise=new Promise(resolve=>release=resolve);const value={promise,release};gates.set(key,value);return value;};
  const counts=key=>requests.filter(row=>row.path===key).length;
  const check=(ok,label)=>{if(!ok)throw Error(label);checks.push(label);};
  const api=/^https?:\/\/[^/]+\/api\//;
  const handler=async route=>{
    const req=route.request(),path=new URL(req.url()).pathname,method=req.method();requests.push({path,method});let data,status=200;
    if(method!=='GET')writes.push({path,method,body:req.postDataJSON()});
    if(path==='/api/auth/me')data=user;
    else if(path===`/api/projects/${projectId}`)data=project;
    else if(path==='/api/training/catalog')data={yolo26:{name:'YOLO26',image:'fixture/yolo',defaults:{epochs:2,batch:4,device:'auto'},environment:{},schema:{properties:{epochs:{type:'integer'},batch:{type:'integer'},device:{type:'string'}}}}};
    else if(path==='/api/training/servers')data=[{id:'default',name:'隔离服务器',enabled:true}];
    else if(path==='/api/training/defaults')data={model_path:'/fixture/model.pt',output_root:''};
    else if(path==='/api/gpu-resources')data={server_name:'fixture',gpus:[{uuid:'GPU-fixture-1',index:0,name:'隔离GPU',memory_free_mib:24576}],processes:[],warnings:[]};
    else if(path===`/api/export/projects/${projectId}/snapshots`)data=[snapshot];
    else if(path==='/api/training/runs'&&method==='POST'){
      const body=req.postDataJSON(),created=makeRun('66666666-6666-4666-8666-666666666666',body.name,body);created.status='draft';runs.push(created);data=created;status=201;
    }else if(path==='/api/training/runs')data={items:runs,total:runs.length,page:1,page_size:20};
    else if(/^\/api\/training\/runs\/[^/]+\/(logs|artifacts)$/.test(path)){
      const holding=gates.get(path),failed=failNext.delete(path),isA=path.includes(idA),name=isA?'任务A':'任务B';
      if(holding)await holding.promise;
      if(failed){status=503;data={detail:`${name}${path.endsWith('/logs')?'日志':'产物'}读取失败`};}
      else data=path.endsWith('/logs')?{text:`${name}最新日志内容`}:{files:[{path:`${name}/best.pt`,bytes:123,role:'weights'}],truncated:false};
    }else if(runs.some(run=>path===`/api/training/runs/${run.id}`))data=runs.find(run=>path.endsWith(run.id));
    else{unexpected.push(`${method} ${path}`);return route.fulfill({status:599,json:{detail:'未配置的隔离API'}});}
    return route.fulfill({status,json:data});
  };
  const logsA=`/api/training/runs/${idA}/logs`,artifactsA=`/api/training/runs/${idA}/artifacts`,artifactsB=`/api/training/runs/${idB}/artifacts`;
  const settle=()=>page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  const responseFor=path=>page.waitForResponse(response=>new URL(response.url()).pathname===path);
  const requestFor=path=>page.waitForRequest(req=>new URL(req.url()).pathname===path);
  const drawer=()=>page.getByRole('dialog').filter({has:page.getByRole('tab',{name:'任务与配置',exact:true})});
  const closeDrawer=async()=>{await page.keyboard.press('Escape');await drawer().waitFor({state:'hidden'});};
  const openRun=async name=>{await page.getByRole('button',{name,exact:true}).click();await drawer().waitFor();};
  const onError=error=>errors.push(error.message);page.on('pageerror',onError);
  await page.addInitScript(user=>{if(!location.protocol.startsWith('http'))return;localStorage.setItem('token','isolated-training-hardening');localStorage.setItem('user',JSON.stringify(user));},user);
  await page.route(api,handler);
  try{
    await page.setViewportSize({width:1440,height:1000});await page.goto(`http://localhost:3000/projects/${projectId}/deliver`);
    await page.getByRole('button',{name:'创建训练任务',exact:true}).click();
    const form=page.getByRole('dialog',{name:'创建训练任务',exact:true});await form.waitFor();
    await form.getByRole('textbox',{name:'任务名称'}).fill('校验修复草稿');
    await form.getByRole('combobox',{name:'冻结版本'}).click();await page.locator('.ant-select-dropdown:visible').getByText(/YOLO ·/).click();
    await form.getByRole('combobox',{name:'物理GPU'}).click();await page.locator('.ant-select-dropdown:visible').getByText(/隔离GPU/).click();
    await form.getByRole('textbox',{name:'任务名称'}).click();
    await form.getByRole('button',{name:'保存任务配置',exact:true}).click();
    const output=form.locator('input[id$="output_root"]');await output.waitFor({state:'visible'});
    await page.waitForFunction(()=>document.activeElement?.id.endsWith('output_root'));
    check(await output.isVisible(),'高级必填输出目录出错时自动展开并聚焦');
    check(writes.length===0,'校验未通过时不提交任务');
    await output.fill('/fixture/runs/fixed');
    const mounts=form.locator('.ant-collapse-header').filter({hasText:'附加数据挂载'});await mounts.click();await form.getByRole('button',{name:'添加挂载',exact:true}).click();
    await mounts.click();await form.locator('.ant-collapse-header').filter({hasText:'高级配置'}).click();
    await form.getByRole('button',{name:'保存任务配置',exact:true}).click();
    const mountSource=form.getByRole('textbox',{name:'服务器目录'});await mountSource.waitFor({state:'visible'});
    await page.waitForFunction(()=>document.activeElement?.id.includes('mounts_0_source'));
    check(await mountSource.isVisible(),'内层挂载字段出错时同时展开高级配置和挂载面板并聚焦');
    await mountSource.fill('/fixture/extra');await form.getByRole('textbox',{name:'容器目录'}).fill('/fixture/data');
    await form.getByRole('button',{name:'保存任务配置',exact:true}).click();await form.waitFor({state:'hidden'});
    check(writes.length===1&&writes[0].body.output_root==='/fixture/runs/fixed'&&writes[0].body.mounts[0].source==='/fixture/extra','修正隐藏错误后能正常保存隔离草稿');
    await closeDrawer();

    await openRun('任务A');
    const delayed=gate(logsA),oldLogRequest=requestFor(logsA);
    await drawer().getByRole('tab',{name:'训练日志',exact:true}).click();const oldLog=await oldLogRequest;
    const artifactGate=gate(artifactsA),artifactResponse=responseFor(artifactsA),artifactRequest=requestFor(artifactsA);
    await drawer().getByRole('tab',{name:'模型产物',exact:true}).click();await artifactRequest;
    check(counts(artifactsA)===1,'日志仍在加载时切产物会立即读取当前产物');
    delayed.release();gates.delete(logsA);await oldLog.response();await settle();
    check((await drawer().getByRole('button',{name:'刷新模型产物',exact:true}).getAttribute('class')).includes('ant-btn-loading'),'旧日志结束不会提前清除当前产物加载状态');
    artifactGate.release();gates.delete(artifactsA);await artifactResponse;
    await drawer().getByRole('cell',{name:'任务A/best.pt',exact:true}).waitFor();
    check(await drawer().getByRole('tab',{name:'模型产物',exact:true}).getAttribute('aria-selected')==='true','过时日志完成不会切走当前产物标签');

    failNext.add(artifactsA);const failedArtifact=responseFor(artifactsA);await drawer().getByRole('button',{name:'刷新模型产物',exact:true}).click();await failedArtifact;
    await drawer().getByRole('alert').filter({hasText:'任务A产物读取失败'}).waitFor();
    check(true,'产物读取失败显示可恢复错误');
    await drawer().getByRole('button',{name:'刷新模型产物',exact:true}).click();await drawer().getByRole('alert').filter({hasText:'任务A产物读取失败'}).waitFor({state:'hidden'});
    check(counts(artifactsA)===3,'刷新产物可重试失败读取并清除旧错误');
    failNext.add(logsA);await drawer().getByRole('tab',{name:'训练日志',exact:true}).click();await drawer().getByRole('alert').filter({hasText:'任务A日志读取失败'}).waitFor();
    await drawer().getByRole('button',{name:'刷新日志',exact:true}).click();await drawer().getByText('任务A最新日志内容',{exact:true}).waitFor();
    check(true,'日志读取失败后能刷新恢复');

    const staleLog=gate(logsA),staleLogRequest=requestFor(logsA);
    await drawer().getByRole('button',{name:'刷新日志',exact:true}).click();const staleLogRead=await staleLogRequest;await closeDrawer();
    await openRun('任务B');await drawer().getByRole('tab',{name:'训练日志',exact:true}).click();await drawer().getByText('任务B最新日志内容',{exact:true}).waitFor();
    staleLog.release();gates.delete(logsA);await staleLogRead.response();await settle();
    check(await drawer().getByText('任务B最新日志内容',{exact:true}).isVisible()&&await drawer().getByText('任务A最新日志内容',{exact:true}).count()===0,'关闭并切任务后旧任务日志响应不会串入新任务');
    await closeDrawer();await openRun('任务A');
    failNext.add(artifactsA);const staleArtifact=gate(artifactsA),staleArtifactRequest=requestFor(artifactsA);
    await drawer().getByRole('tab',{name:'模型产物',exact:true}).click();const staleArtifactRead=await staleArtifactRequest;await closeDrawer();
    await openRun('任务B');await drawer().getByRole('tab',{name:'模型产物',exact:true}).click();await drawer().getByRole('cell',{name:'任务B/best.pt',exact:true}).waitFor();
    staleArtifact.release();gates.delete(artifactsA);await staleArtifactRead.response();await settle();
    check(await drawer().getByRole('cell',{name:'任务B/best.pt',exact:true}).isVisible()&&await drawer().getByRole('alert').filter({hasText:'任务A产物读取失败'}).count()===0,'旧任务产物失败响应不会污染新任务结果或错误');
    check(await page.getByText('任务A产物读取失败',{exact:true}).count()===0,'过时请求不会经全局toast向新任务显示旧错误');
    check(!!oldLog.failure()&&!!staleLogRead.failure()&&!!staleArtifactRead.failure(),'切标签或切任务会取消过时的读取请求');
    await page.screenshot({path:'output/playwright/training-hardening-task-isolation.png',fullPage:true,animations:'disabled'});
    check(writes.length===1&&writes.every(row=>row.path==='/api/training/runs'&&row.method==='POST'),'只保存一个模拟草稿，无训练或真实数据写入');
    check(unexpected.length===0&&errors.length===0,'所有API均严格拦截且无浏览器运行时异常');
    return {checks,requests,writes,unexpected,errors,scope:'all API requests intercepted; only one mock draft saved; no real training or business writes'};
  }catch(error){await page.screenshot({path:'output/playwright/training-hardening-failure.png',fullPage:true});throw error;}finally{for(const pending of gates.values())pending.release();await page.goto('about:blank');await page.unroute(api,handler);page.off('pageerror',onError);}
}
