async function verify(page,origin='http://127.0.0.1:3108') {
  await page.unrouteAll();page.setDefaultTimeout(12000);await page.setViewportSize({width:1280,height:900});
  await page.addInitScript(()=>{if(location.protocol==='http:'){localStorage.setItem('token','isolated-fixture');localStorage.setItem('user',JSON.stringify({role:'admin',username:'fixture'}));}});
  const now=new Date().toISOString(),writes=[],calls=[];
  const run={id:'source',name:'Fixture large model',kind:'model_merge',status:'completed',source:{model_kind:'swift_sft'},config:{parameters:{},output_subdir:'result'},server:{name:'source'},output_dir:'/export',runtimes:['triton_vllm']};
  const releases=[{id:'release-one',run_id:run.id,name:'Detector',version:'v1',runtime:'triton_plan',status:'ready',created_at:now,source:{artifact_path:'model.engine'},archive:{path:'F:/archive/model.zip',bytes:7,sha256:'a'.repeat(64),archived_at:now}}];
  const repository=[{id:'repo-one',name:'Warehouse detector',version:'v3',format:'tensorrt',gpu_model:'NVIDIA T4',tensorrt_version:'10.8.0',batch_profile:'fixed:8',status:'ready',entry_file:'detector.engine',file_size:7,files:[],created_at:now},
    {id:'repo-missing',name:'Missing build version',version:'v1',format:'tensorrt',gpu_model:'NVIDIA T4',tensorrt_version:'',status:'ready',files:[]}];
  const target=(id,kind,server)=>({id,name:id,kind,target:{environment:kind==='model'?'docker':'docker_kafka',runtime:'triton_plan',file_server:server,control_server:server,repository:'/data/models',model_name:'detector',containers:[kind==='model'?'triton':'nexusai-worker'],namespace:'ai',container:'triton-server'},current:{state:'unmanaged'}});
  const targets=[target('model-one','model','control-one'),target('model-two','model','control-two'),target('image-one','image','control-one'),target('image-two','image','control-two')];
  let records=[],batchIndex=0,userRole='admin',failCopyOnce=true;
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.route('**/api/**',async route=>{
    const request=route.request(),url=new URL(request.url()),path=url.pathname,method=request.method();if(!path.startsWith('/api/'))return route.continue();
    calls.push({method,path});if(method!=='GET')writes.push({method,path,input:request.postData()?request.postDataJSON():null});
    if(path==='/api/auth/me')return route.fulfill({json:{id:1,tenant_id:1,username:'fixture',role:userRole,is_active:true,created_at:now}});
    if(path==='/api/model-repository')return route.fulfill({json:{items:repository,total:repository.length,page:1,page_size:20,gpu_models:['NVIDIA T4']}});
    if(path==='/api/model-repository/repo-one')return route.fulfill({json:repository[0]});
    if(path.endsWith('/logs'))return route.fulfill({json:{content:'fixture update log',truncated:false,source:'updates',server:'control-one',status:'staged',collected_at:now,instances:[],selected_instance:null}});
    if(path==='/api/model-deployments/servers')return route.fulfill({json:[{name:'control-one',host:'10.0.0.1',user:'root',port:22},{name:'control-two',host:'10.0.0.2',user:'root',port:22}]});
    if(path==='/api/model-deployments/servers/discover')return route.fulfill({json:{containers:['nexusai-worker3'],models:[],triton_config:''}});
    if(path==='/api/model-deployments/sources')return route.fulfill({json:{items:[run],has_more:false}});
    if(path==='/api/training/runs/source')return route.fulfill({json:run});
    if(path==='/api/training/runs/source/artifacts')return route.fulfill({json:{files:[{path:'merged/config.json',bytes:7}],truncated:false}});
    if(path==='/api/model-deployments/releases'){
      if(method==='GET')return route.fulfill({json:{items:releases,has_more:false}});
      const input=request.postDataJSON(),row={...releases[0],...input,id:'release-two',status:'ready'};releases.unshift(row);return route.fulfill({status:201,json:{...row,status:'archiving'}});
    }
    if(path==='/api/model-deployments/targets'){
      if(method==='GET')return route.fulfill({json:{items:targets}});
      const input=request.postDataJSON();
      if(input.name==='Batch worker'&&input.target.control_server==='control-two'&&failCopyOnce){failCopyOnce=false;return route.fulfill({status:409,json:{detail:'fixture duplicate'}});}
      const row={...input,id:targets.length===4?'new-target':'new-target-'+targets.length,current:{state:'unmanaged'}};targets.push(row);return route.fulfill({status:201,json:row});
    }
    if(path==='/api/model-deployments/targets/new-target'&&method==='PUT'){
      const input=request.postDataJSON(),index=targets.findIndex(row=>row.id==='new-target');targets[index]={...input,id:'new-target',current:{state:'unmanaged'}};return route.fulfill({json:targets[index]});
    }
    if(path==='/api/model-deployments/batches'){
      const input=request.postDataJSON(),batch='batch-'+(++batchIndex),release=input.repository_id?repository.find(row=>row.id===input.repository_id):releases.find(row=>row.id===input.release_id);
      const rows=input.target_ids.map((id,index)=>{const selected=targets.find(row=>row.id===id);return {id:batch+'-'+index,batch_id:batch,kind:input.kind,status:'draft',target:selected.target,target_id:id,created_at:now,
        source:input.kind==='model'?{name:release.name,version:release.version}:{image:input.image},inspection:{},logs:[]};});
      records=[...rows,...records];return route.fulfill({status:201,json:{batch_id:batch,items:rows}});
    }
    if(path==='/api/model-deployments')return route.fulfill({json:{items:records.filter(row=>!url.searchParams.get('batch_id')||row.batch_id===url.searchParams.get('batch_id')),has_more:false}});
    const batchMatch=path.match(/^\/api\/model-deployments\/batches\/([^/]+)(?:\/([^/]+))?$/);
    if(batchMatch){
      const batch=batchMatch[1],operation=batchMatch[2],rows=records.filter(row=>row.batch_id===batch);
      if(method==='GET')return route.fulfill({json:{batch_id:batch,items:rows}});
      for(const [index,row] of rows.entries()){
        row.status=operation==='transfer'?'staged':operation==='rollback'?'rolled_back':row.kind==='model'&&index===1?'activation_failed':'active';
        if(operation==='transfer'&&row.target.environment==='k8s'){
          row.inspection.preload_info={playbook:'/opt/jx_k8s/ansible/preload-ai-images.yml',inventory:'/opt/jx_k8s/ansible/complete.json',node_count:2};
          row.inspection.image_receipt={node_count:2,pinned_image:'registry/service@sha256:'+'a'.repeat(64),archive_sha256:'b'.repeat(64),nodes:['k8s-node-one','k8s-node-two'].map(node=>({node,uid:node,digest:'sha256:'+'a'.repeat(64),archive_sha256:'b'.repeat(64),ok:true}))};
        }
        if(operation==='activate'){
          row.inspection.instances=[{name:row.target.containers[0]||row.target.workload+'-0',uid:row.id+'-uid',ok:row.status==='active',image:row.source.image||'triton:v1',image_id:'sha256:'+row.id}];
          const profile=targets.find(value=>value.id===row.target_id);profile.current={state:row.status==='active'?'verified':'unknown',version:row.source.version,image:row.source.image,checked_at:now,instances:row.inspection.instances};profile.latest=row;
          if(row.status!=='active')row.error='fixture: one instance did not load';
        }
      }
      return route.fulfill({status:202,json:{results:rows.map(row=>({id:row.id,status:row.status}))}});
    }
    const detailMatch=path.match(/^\/api\/model-deployments\/([^/]+)(?:\/([^/]+))?$/);
    if(detailMatch){const row=records.find(row=>row.id===detailMatch[1]);if(!row)throw Error('Missing record '+path);
      if(method==='POST'&&detailMatch[2]==='rollback')row.status='rolled_back';
      return route.fulfill({json:row});}
    throw Error('Unexpected API '+method+' '+path);
  });
  let stage='open';
  try{
    await page.goto(origin+'/model-deployments?legacy=1');
    if(await page.getByRole('tab',{name:'模型版本库',exact:true}).count())throw Error('Duplicate model library remains');
    await page.getByRole('menuitem',{name:'模型与镜像更新',exact:true}).waitFor();
    await page.getByRole('combobox',{name:'待下发仓库模型',exact:true}).click();
    await page.locator('.ant-select-item-option-disabled').getByText(/Missing build version/).waitFor();
    await page.keyboard.press('Escape');
    stage='archive';await page.getByRole('tab',{name:'大模型完整目录归档',exact:true}).click();
    await page.getByRole('combobox',{name:/来源训练 \/ 合并任务$/}).click();await page.getByText('Fixture large model · model_merge',{exact:true}).click();
    await page.getByRole('textbox',{name:/版本号$/}).fill('v2');await page.getByRole('button',{name:'拉取并保存版本',exact:true}).click();
    await page.getByText('v2',{exact:true}).waitFor();
    if(writes.length!==1||!writes[0].path.endsWith('/releases'))throw Error('Archiving changed service state');
    stage='multi-model';await page.goto(origin+'/model-deployments?legacy=1&repository_id=repo-one');
    await page.getByText('Warehouse detector · v3 · NVIDIA T4 · TRT 10.8.0 · 固定 batch 8',{exact:true}).waitFor();
    for(const id of ['model-one','model-two'])await page.getByRole('row').filter({has:page.getByText(id,{exact:true})}).getByRole('checkbox').check();
    await page.getByRole('button',{name:/一键准备$/}).click();
    const batchInput=writes.find(row=>row.path==='/api/model-deployments/batches').input;
    if(batchInput.repository_id!=='repo-one'||batchInput.release_id)throw Error('Model batch must directly reference repository');

    await page.getByText('待生效',{exact:true}).first().waitFor();
    if(writes.some(row=>row.path.endsWith('/activate')))throw Error('Prepare activated services automatically');
    await page.getByRole('button',{name:'确认生效',exact:true}).click();await page.getByRole('dialog').waitFor();
    await page.getByRole('dialog').getByRole('button',{name:/^返\s*回$/}).click();await page.getByRole('dialog').waitFor({state:'hidden'});
    if(writes.some(row=>row.path.endsWith('/activate')))throw Error('Cancel performed activation');
    await page.getByRole('button',{name:'确认生效',exact:true}).click();
    const dialog=page.getByRole('dialog');await dialog.getByText(/control-one/).waitFor();await dialog.getByText(/control-two/).waitFor();
    await dialog.getByRole('button',{name:'确认生效',exact:true}).click();await dialog.waitFor({state:'hidden'});
    await page.getByRole('tabpanel',{name:'下发与镜像记录'}).getByText('生效未完成',{exact:true}).waitFor();await page.getByRole('tabpanel',{name:'下发与镜像记录'}).getByText('已生效',{exact:true}).waitFor();
    await page.getByRole('button',{name:'查看详情',exact:true}).first().click();await page.getByRole('dialog').getByText('模型版本',{exact:true}).waitFor();
    await page.getByRole('dialog').getByRole('button',{name:/Close|关闭/}).click();
    await page.getByRole('dialog').waitFor({state:'hidden'});
    stage='multi-image';await page.getByRole('tab',{name:'镜像更新 / 旧版模型下发',exact:true}).click();
    await page.getByRole('combobox',{name:'批量操作类型',exact:true}).click();await page.getByText('镜像更新',{exact:true}).last().click();
    await page.getByRole('textbox',{name:'目标镜像版本',exact:true}).fill('registry/service:v20261024');
    for(const id of ['image-one','image-two'])await page.getByRole('row').filter({has:page.getByText(id,{exact:true})}).getByRole('checkbox').check();
    await page.getByRole('button',{name:/一键准备$/}).click();
    await page.getByText('待生效',{exact:true}).first().waitFor();await page.getByRole('button',{name:'确认生效',exact:true}).click();
    await page.getByRole('dialog').getByRole('button',{name:'确认生效',exact:true}).click();await page.getByRole('dialog').waitFor({state:'hidden'});
    await page.getByRole('tabpanel',{name:'下发与镜像记录'}).getByText('已生效',{exact:true}).first().waitFor();
    const batches=writes.filter(row=>row.path==='/api/model-deployments/batches');
    if(batches.length!==2||batches.some(row=>row.input.target_ids.length!==2)||batches[1].input.kind!=='image')throw Error('Incorrect multi-target batches');
    stage='target-form';await page.getByRole('tab',{name:'镜像更新 / 旧版模型下发',exact:true}).click();await page.getByText('添加服务器目标',{exact:true}).click();
    await page.getByRole('textbox',{name:/目标名称$/}).fill('New worker');await page.getByRole('combobox',{name:'目标用途',exact:true}).click();await page.getByText('镜像更新',{exact:true}).last().click();
    await page.getByRole('combobox',{name:/部署环境$/}).click();await page.getByText('Docker Kafka worker',{exact:true}).last().click();
    await page.getByRole('combobox',{name:/SSH服务器$/}).click();await page.getByText('control-one · 10.0.0.1:22',{exact:true}).last().click();
    await page.getByRole('combobox',{name:/待更新镜像的全部容器$/}).fill('nexusai-worker3');await page.keyboard.press('Enter');await page.getByRole('button',{name:/保存服务器目标$/}).click();
    await page.getByText('New worker',{exact:true}).waitFor();
    const saved=writes.find(row=>row.path==='/api/model-deployments/targets');if(saved.input.target.file_server||saved.input.target.repository||saved.input.kind!=='image')throw Error('Image form leaked model fields');
    await page.getByRole('row').filter({has:page.getByText('New worker',{exact:true})}).getByRole('button',{name:'编辑目标',exact:true}).click();
    await page.getByRole('textbox',{name:/目标名称$/}).fill('New worker corrected');await page.getByRole('combobox',{name:/部署环境$/}).click();await page.getByText('K8s',{exact:true}).last().click();
    await page.getByRole('textbox',{name:/工作负载名称$/}).fill('nexusai-triton-prod');await page.getByRole('textbox',{name:/待更新镜像的容器名$/}).fill('nexusai');
    await page.getByRole('button',{name:/保存服务器目标$/}).click();await page.getByText('New worker corrected',{exact:true}).waitFor();
    const edited=writes.find(row=>row.method==='PUT');if(edited.input.target.environment!=='k8s'||edited.input.target.containers.length||edited.input.target.container!=='nexusai')throw Error('Incorrect K8s image target');
    stage='k8s-preload';await page.getByRole('row').filter({has:page.getByText('New worker corrected',{exact:true})}).getByRole('checkbox').check();
    await page.getByRole('button',{name:/一键准备$/}).click();
    await page.getByRole('tabpanel',{name:'下发与镜像记录'}).getByText('待生效',{exact:true}).waitFor();await page.getByRole('button',{name:'查看详情',exact:true}).click();
    await page.getByRole('dialog').getByText('Ansible预分发',{exact:true}).waitFor();await page.getByText('查看全部 2 个节点的预分发结果',{exact:true}).click();
    await page.getByRole('dialog').getByText('k8s-node-one',{exact:true}).waitFor();await page.getByRole('dialog').getByText('k8s-node-two',{exact:true}).waitFor();
    await page.getByRole('dialog').getByRole('button',{name:/Close|关闭/}).click();await page.getByRole('dialog').waitFor({state:'hidden'});
    await page.getByRole('button',{name:'确认生效',exact:true}).click();await page.getByRole('dialog').getByRole('button',{name:'确认生效',exact:true}).click();await page.getByRole('dialog').waitFor({state:'hidden'});
    await page.getByRole('tabpanel',{name:'下发与镜像记录'}).getByText('已生效',{exact:true}).waitFor();await page.getByRole('tab',{name:'镜像更新 / 旧版模型下发',exact:true}).click();
    stage='multi-target-create';await page.getByText('添加服务器目标',{exact:true}).click();
    await page.getByRole('textbox',{name:/目标名称$/}).fill('Batch worker');await page.getByRole('combobox',{name:'目标用途',exact:true}).click();await page.getByText('镜像更新',{exact:true}).last().click();
    await page.getByRole('combobox',{name:/部署环境$/}).click();await page.getByText('Docker Kafka worker',{exact:true}).last().click();
    await page.getByRole('combobox',{name:/SSH服务器$/}).click();await page.getByText('control-one · 10.0.0.1:22',{exact:true}).last().click();
    await page.getByRole('combobox',{name:/同时添加到其他服务器$/}).click();await page.getByText('control-two · 10.0.0.2:22',{exact:true}).last().click();
    await page.getByRole('combobox',{name:/待更新镜像的全部容器$/}).fill('nexusai-worker3');await page.keyboard.press('Enter');await page.getByRole('button',{name:/保存服务器目标$/}).click();
    await page.getByText('已保存 1 个服务器目标',{exact:false}).waitFor();
    await page.getByText(/control-two：.*fixture duplicate/).waitFor();
    await page.getByRole('button',{name:/保存服务器目标$/}).click();
    await page.getByRole('combobox',{name:/同时添加到其他服务器$/}).waitFor({state:'hidden'});
    const copies=writes.filter(row=>row.path==='/api/model-deployments/targets'&&row.input.name==='Batch worker');
    if(copies.length!==3||copies.filter(row=>row.input.target.control_server==='control-one').length!==1||copies.filter(row=>row.input.target.control_server==='control-two').length!==2||targets.filter(row=>row.name==='Batch worker').length!==2||copies.some(row=>row.input.target.file_server))throw Error('Multi-server target creation did not preserve failed server for retry');
    stage='twenty-platform-prepare';for(let i=3;i<=20;i++)targets.push(target('model-'+i,'model','platform-'+i));
    await page.goto(origin+'/model-deployments?legacy=1&repository_id=repo-one');
    await page.getByText('Warehouse detector · v3 · NVIDIA T4 · TRT 10.8.0 · 固定 batch 8',{exact:true}).waitFor();
    await page.getByRole('table').getByRole('checkbox').first().check();
    await page.getByRole('button',{name:/一键准备$/}).click();
    await page.getByText('已提交 20 个平台的准备任务',{exact:false}).waitFor();
    await page.getByRole('button',{name:'确认生效',exact:true}).waitFor();
    const twenty=writes.filter(row=>row.path==='/api/model-deployments/batches').at(-1);
    if(twenty.input.target_ids.length!==20||records.filter(row=>row.batch_id==='batch-4'&&row.status==='staged').length!==20||writes.some(row=>row.path==='/api/model-deployments/batches/batch-4/activate'))throw Error('Twenty platforms were not prepared without activation');
    await page.screenshot({path:'output/playwright/distribution-center-desktop.png',fullPage:true});
    stage='mobile';await page.setViewportSize({width:390,height:844});
    await page.waitForFunction(()=>document.documentElement.scrollWidth<=innerWidth+1,{timeout:5000});
    await page.screenshot({path:'output/playwright/distribution-center-mobile.png',fullPage:true});
    const before=calls.length;await page.evaluate(()=>localStorage.setItem('user',JSON.stringify({role:'annotator',username:'fixture'})));
    // Init script sets admin for a fresh document, so verify the regular user's guard in an isolated module fixture.
    await page.route('**/src/App.tsx*',async route=>{
      const source=await (await page.request.get(origin+'/src/pages/ModelDeploymentsPage.tsx')).text(),react=source.match(/["']([^"']*\/react\.js\?[^"']+)["']/)[1],router=source.match(/["']([^"']*\/react-router-dom\.js\?[^"']+)["']/)[1];
      await route.fulfill({contentType:'application/javascript',body:`import React from '${react}';import {MemoryRouter} from '${router}';import Provider from '/src/ui/PlatformProvider.tsx';import Page from '/src/pages/ModelDeploymentsPage.tsx';localStorage.setItem('user',JSON.stringify({role:'annotator'}));export default function App(){return React.createElement(Provider,null,React.createElement(MemoryRouter,null,React.createElement(Page)));}`});
    });
    await page.reload();await page.getByText('模型下发及镜像更新仅管理员可用',{exact:true}).waitFor();
    if(calls.length!==before)throw Error('Non-admin called deployment APIs');
    if(errors.length)throw Error('Browser errors: '+errors.join('; '));
    return {passed:true,twenty_platform_prepare:true,repository_direct_reference:true,repository_deep_link:true,missing_build_version_disabled:true,large_model_archive:true,multi_model:2,multi_image:2,partial_failure:true,manual_activation:true,cancel_no_write:true,target_create_edit:true,k8s_image_form:true,k8s_preload_flow:true,mobile_width:390,admin_guard:true,remote_writes:0,runtime_errors:errors};
  }catch(error){await page.screenshot({path:'output/playwright/distribution-center-failure.png',fullPage:true});throw Error(stage+': '+error.message);}
  finally{await page.goto('about:blank');await page.unrouteAll();}
}
module.exports=verify;
if(require.main===module){
  const fs=require('node:fs'),path=require('node:path');
  const pw=require(path.join(process.env.USERPROFILE,'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));
  (async()=>{const browser=await pw.chromium.launch({channel:'msedge',headless:true});
    try{const context=await browser.newContext({serviceWorkers:'block'});const result=await verify(await context.newPage(),process.argv[2]);
      fs.writeFileSync('output/playwright/distribution-center-verification.json',JSON.stringify(result,null,2));console.log(JSON.stringify(result,null,2));}
    finally{await browser.close();}
  })().catch(error=>{console.error(error);process.exitCode=1;});
}

