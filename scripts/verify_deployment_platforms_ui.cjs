const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const pw=require(path.join(process.env.USERPROFILE,'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));
async function verify(page,origin){
  await page.addInitScript(()=>{localStorage.setItem('token','isolated-fixture');localStorage.setItem('user',JSON.stringify({role:'admin'}));});
  const now=new Date().toISOString(),errors=[],writes=[],scans=[];page.on('pageerror',error=>errors.push(error.message));
  const model=(id,name,version)=>({id,name,version,format:'tensorrt',status:'ready',gpu_model:'Tesla T4',tensorrt_version:'10.14.1',entry_file:'model.plan',files:[]});
  const versions=[model('d1','detector','v1'),model('p1','parcel','v1'),model('d2','detector','v2'),model('p2','parcel','v2')];
  const key=value=>crypto.createHash('sha256').update(value).digest('hex');
  const platforms=Array.from({length:20},(_,i)=>({id:'platform-'+i,name:'Platform '+i,config:{environment:'docker',control_server:'server-'+i,file_server:'server-'+i,namespace:'ai',services:[]},models:['detector',i===0?'parcel_live':'parcel'].map(name=>({key:key(i+name),name,target:{environment:'docker',runtime:'triton_plan',control_server:'server-'+i,file_server:'server-'+i,repository:'/models',model_name:name,containers:['triton']}})),versions:{},stale:false,mappings:{}}));
  let records=[],batchIndex=0;
  const plan=input=>{const items=input.platform_ids.flatMap(id=>input.versions.map(selection=>{
    const platform=platforms.find(row=>row.id===id),version=versions.find(row=>row.id===selection.repository_id),version_key='r:'+version.id;
    const selected=input.assignments.find(row=>row.platform_id===id&&row.version_key===version_key)?.model_key||platform.mappings[version.name]||platform.models.find(row=>row.name===version.name)?.key||null;
    const gpu=platform.gpu||'Tesla T4',trt=platform.trt||'10.14.1',unknown=platform.unknown;
    const compatible=version.gpu_model===gpu&&version.tensorrt_version===trt;
    const state=unknown?'unknown':compatible?'compatible':'incompatible';
    const message=unknown?'GPU/TensorRT环境未核验或已过期':compatible?'GPU型号和TensorRT版本匹配':`所选构建需要 ${version.gpu_model} / TRT ${version.tensorrt_version}；目标环境 ${gpu} / TRT ${trt}`;
    return {platform_id:id,platform_name:platform.name,version_key,name:version.name,version:version.version,built_gpu:version.gpu_model,built_trt:version.tensorrt_version,compatibility:selected==='skip'?'skipped':state,compatibility_message:message,selected_key:selected,error:platform.checked_at?(selected==='skip'||compatible&&!unknown?null:message):'请先读取平台模型',options:platform.models.map(row=>({key:row.key,name:row.name,repository:'/models',service:'triton',disabled:!compatible||unknown,compatibility:state,message,gpus:[gpu],tensorrt_versions:[trt]}))};
  }));
    for(const row of items)if(row.compatibility==='incompatible'&&!input.assignments.some(a=>a.platform_id===row.platform_id&&a.version_key===row.version_key)&&items.some(other=>other.platform_id===row.platform_id&&other.name===row.name&&other.compatibility==='compatible'&&other.selected_key))Object.assign(row,{selected_key:'skip',error:null,compatibility:'skipped',auto_skipped:true,compatibility_message:'已自动跳过不兼容构建'});
    return items;
  };
  await page.route('**/api/**',async route=>{
    const request=route.request(),url=new URL(request.url()),p=url.pathname,method=request.method();
    if(!p.startsWith('/api/'))return route.continue();
    const input=request.postData()?request.postDataJSON():null;if(method!=='GET')writes.push({p,method,input});
    if(p==='/api/auth/me')return route.fulfill({json:{id:1,role:'admin',is_active:true,username:'fixture'}});
    if(p==='/api/model-repository')return route.fulfill({json:{items:versions,total:versions.length,gpu_models:['Tesla T4','NVIDIA A10']}});
    if(p==='/api/model-deployments/servers')return route.fulfill({json:platforms.map(row=>({name:row.config.control_server,host:'10.0.0.1',port:22,user:'u'}))});
    if(p==='/api/model-deployments/platforms')return route.fulfill({json:{items:platforms}});
    if(p==='/api/model-deployments/platforms/preview')return route.fulfill({json:{items:plan(input)}});
    const scan=p.match(/\/platforms\/(platform-\d+)\/discover$/);
    if(scan){const row=platforms.find(row=>row.id===scan[1]);row.checked_at=now;scans.push(row.id);return route.fulfill({json:row});}
    if(p==='/api/model-deployments/platforms/batches'){
      const items=plan(input);if(items.some(row=>!row.selected_key||row.error))throw Error('Unresolved plan submitted');
      const batch_id='multi-'+(++batchIndex);
      const jobs=items.filter(row=>row.selected_key!=='skip').map((row,i)=>{const platform=platforms.find(p=>p.id===row.platform_id),slot=platform.models.find(m=>m.key===row.selected_key);platform.mappings[row.name]=row.selected_key;
        return {id:batch_id+'-'+i,batch_id,kind:'model',status:'draft',deletable:true,target:{...slot.target,platform_id:platform.id},source:{name:row.name,version:row.version,gpu_model:row.built_gpu},inspection:{},logs:[],created_at:now};});
      records.unshift(...jobs);return route.fulfill({status:201,json:{batch_id,items:jobs}});
    }
    const batch=p.match(/\/batches\/(multi-\d+)(?:\/(\w+))?$/);
    if(batch){const jobs=records.filter(row=>row.batch_id===batch[1]);if(method==='GET')return route.fulfill({json:{batch_id:batch[1],items:jobs}});
      jobs.forEach(row=>{row.status=batch[2]==='transfer'?'staged':'active';row.deletable=row.status==='staged';});return route.fulfill({json:{results:jobs.map(row=>({id:row.id,status:row.status}))}});}
    if(p==='/api/model-deployments'&&method==='DELETE'){
      if(records.some(row=>input.ids.includes(row.id)&&!row.deletable))throw Error('Attempted to delete protected history');
      records=records.filter(row=>!input.ids.includes(row.id));return route.fulfill({json:{deleted:input.ids}});
    }
    if(p==='/api/model-deployments'){const jobs=records.filter(row=>!url.searchParams.get('batch_id')||row.batch_id===url.searchParams.get('batch_id'));return route.fulfill({json:{items:jobs.slice(0,20),has_more:jobs.length>20}});}
    if(['/api/model-deployments/sources','/api/model-deployments/releases','/api/model-deployments/targets'].includes(p))return route.fulfill({json:{items:[],has_more:false}});
    throw Error('Unexpected API '+method+' '+p);
  });
  const choose=async version=>{const box=page.getByRole('combobox',{name:'本次模型版本',exact:true});await box.fill(version.name);await page.getByText(`${version.name} · ${version.version} · ${version.gpu_model} · TRT ${version.tensorrt_version}`,{exact:true}).last().click();await page.keyboard.press('Escape');};
  await page.goto(origin+'/model-deployments');
  const selectorWidths=[];
  const checkVersionWidth=async label=>{
    const geometry=await page.getByRole('combobox',{name:'本次模型版本',exact:true}).evaluate(input=>{
      const field=input.closest('.ant-select'),parent=field.parentElement;
      return {field:field.getBoundingClientRect().width,available:parent.getBoundingClientRect().width};
    });
    if(geometry.field<geometry.available*.95)throw Error(`${label}: model version selector collapsed (${geometry.field}/${geometry.available})`);
    selectorWidths.push({label,...geometry});
  };
  await checkVersionWidth('desktop empty');
  await page.screenshot({path:'output/playwright/deployment-selector-desktop.png'});
  await page.setViewportSize({width:390,height:844});await checkVersionWidth('mobile empty');
  await page.screenshot({path:'output/playwright/deployment-selector-mobile.png'});
  await page.setViewportSize({width:1280,height:900});
  await choose(versions[0]);await choose(versions[1]);
  await checkVersionWidth('desktop selected');
  await page.setViewportSize({width:390,height:844});await checkVersionWidth('mobile selected');
  await page.waitForFunction(()=>document.documentElement.scrollWidth<=innerWidth+1);
  await page.setViewportSize({width:1280,height:900});
  await page.getByRole('table').first().getByRole('checkbox').first().check();
  const mapping=page.getByRole('combobox',{name:'Platform 0 parcel 对应模型',exact:true});await mapping.waitFor();
  if(await page.getByRole('button',{name:'一键准备所选更新',exact:true}).isEnabled())throw Error('Unmapped model allowed preparation');
  await mapping.click();await page.getByText('parcel_live · triton · /models · Tesla T4 / TRT 10.14.1',{exact:true}).click();
  await page.getByRole('button',{name:'一键准备所选更新',exact:true}).click();
  await page.getByText(/20 个平台、40 项更新/).waitFor();
  if(records.length!==40||scans.length!==20||writes.some(row=>row.p.endsWith('/activate')))throw Error('Incorrect 20x2 preparation');
  await page.getByRole('button',{name:'确认生效',exact:true}).click();await page.getByRole('dialog').getByRole('button',{name:/返\s*回/}).click();
  if(writes.some(row=>row.p.endsWith('/activate')))throw Error('Cancel activated jobs');
  await page.getByRole('button',{name:'确认生效',exact:true}).click();await page.getByRole('dialog').getByRole('button',{name:'确认生效',exact:true}).click();
  await page.getByText(/已生效 40/).waitFor();
  await page.reload();await choose(versions[2]);await choose(versions[3]);await page.getByRole('table').first().getByRole('checkbox').first().check();
  await page.getByRole('button',{name:'一键准备所选更新',exact:true}).click();await page.getByText(/20 个平台、40 项更新/).waitFor();
  if(records.length!==80||scans.length!==20||writes.some(row=>row.p==='/api/model-deployments/platforms'&&row.method==='POST'))throw Error('Next version did not reuse platform and model mappings');
  await page.getByRole('button',{name:'删除记录',exact:true}).first().click();await page.getByRole('button',{name:/取\s*消/}).click();
  if(writes.some(row=>row.method==='DELETE'))throw Error('Cancel deleted records');
  await page.getByRole('button',{name:'删除记录',exact:true}).first().click();await page.getByRole('button',{name:'确认删除',exact:true}).click();
  await page.getByText('已删除 1 条未生效记录。',{exact:true}).waitFor();
  await page.getByRole('table').getByRole('checkbox').first().check();
  await page.getByRole('button',{name:'删除所选未生效记录',exact:true}).click();await page.getByRole('button',{name:'确认删除',exact:true}).click();
  await page.getByText('已删除 20 条未生效记录。',{exact:true}).waitFor();
  if(records.length!==59||records.filter(row=>row.status==='active').length!==40)throw Error('Record deletion affected active history');
  platforms[1].gpu='NVIDIA A10';
  const a10={...model('a10','detector','v2-a10'),gpu_model:'NVIDIA A10'},wrongTrt={...a10,id:'bad-trt',version:'bad-trt',tensorrt_version:'10.8.0'};
  versions.push(a10,wrongTrt);
  await page.reload();await choose(versions[2]);
  await page.getByRole('row').filter({has:page.getByText('Platform 1',{exact:true})}).getByRole('checkbox').check();
  await page.getByText(/所选构建需要 Tesla T4.*NVIDIA A10/).first().waitFor();
  if(await page.getByRole('button',{name:'一键准备所选更新',exact:true}).isEnabled())throw Error('Incompatible GPU allowed preparation');
  await choose(a10);await page.getByText(/已按GPU和TensorRT自动匹配/).waitFor();
  await page.getByRole('button',{name:'一键准备所选更新',exact:true}).click();
  await page.getByText(/1 个平台、1 项更新/).waitFor();
  if(records[0].source.gpu_model!=='NVIDIA A10'||records.length!==60)throw Error('Automatic GPU variant routing failed');
  await page.reload();await choose(wrongTrt);await page.getByRole('row').filter({has:page.getByText('Platform 1',{exact:true})}).getByRole('checkbox').check();
  await page.getByText(/TRT 10.8.0.*TRT 10.14.1/).first().waitFor();
  if(await page.getByRole('button',{name:'一键准备所选更新',exact:true}).isEnabled())throw Error('Incompatible TensorRT allowed preparation');
  await page.screenshot({path:'output/playwright/deployment-platforms-desktop.png',fullPage:true});
  await page.setViewportSize({width:390,height:844});await page.waitForFunction(()=>document.documentElement.scrollWidth<=innerWidth+1);await page.screenshot({path:'output/playwright/deployment-platforms-mobile.png',fullPage:true});
  if(errors.length)throw Error(errors.join('; '));
  return {passed:true,selector_widths:selectorWidths,platforms:20,models:2,jobs_per_batch:40,gpu_variant_auto_match:true,incompatible_gpu_blocked:true,incompatible_trt_blocked:true,tomorrow_reuses_platforms:true,mapping_persisted:true,automatic_discovery:true,manual_activation:true,cancel_no_activation:true,unapplied_record_delete:true,cancel_delete_no_write:true,active_history_preserved:true,remote_writes:0,runtime_errors:errors};
}
(async()=>{const browser=await pw.chromium.launch({channel:'msedge',headless:true});try{const context=await browser.newContext({viewport:{width:1280,height:900},serviceWorkers:'block'});const result=await verify(await context.newPage(),process.argv[2]||'http://127.0.0.1:3108');fs.writeFileSync('output/playwright/deployment-platforms-verification.json',JSON.stringify(result,null,2));console.log(JSON.stringify(result,null,2));}finally{await browser.close();}})().catch(error=>{console.error(error);process.exitCode=1;});
