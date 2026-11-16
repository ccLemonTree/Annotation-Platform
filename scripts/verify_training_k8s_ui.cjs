const assert=require('node:assert/strict'),path=require('node:path');
const {chromium}=require(path.join(process.env.USERPROFILE,'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));
(async()=>{const browser=await chromium.launch({channel:'msedge',headless:true});try{
 const page=await browser.newPage(),errors=[],writes=[],logs=[];page.on('pageerror',e=>errors.push(e.message));
 await page.addInitScript(()=>{localStorage.setItem('user',JSON.stringify({role:'admin'}));localStorage.setItem('token','fixture');});
 const names={yolo26:'YOLO26',rfdetr_medium:'RF-DETR Medium',rfdetr_2xlarge:'RF-DETR 2XL',wemm:'WeMM'};
 const catalog=Object.fromEntries(Object.entries(names).map(([k,name])=>[k,{name,image:'docker:1',defaults:k==='yolo26'?{device:'auto'}:k.startsWith('rfdetr')?{devices:'auto'}:{learning_rate:0.00001,task_type:'embedding',loss_type:'infonce'},environment:k==='wemm'?{IMAGE_MAX_TOKEN_NUM:'1024'}:{},schema:{properties:k==='yolo26'?{device:{type:'string'}}:k.startsWith('rfdetr')?{devices:{type:'string'}}:{learning_rate:{type:'number'},task_type:{type:'string'},loss_type:{type:'string'}}}}]));
 const root='/adb-ssd-mount/ai/chen/training',server={id:'default',name:'Docker',enabled:true},ks={id:'jiashan-k8s',name:'嘉善移动'},nodes=['node-a','node-b'];let run;
 await page.route('**/api/**',route=>{const req=route.request(),u=new URL(req.url()),p=u.pathname,json=body=>route.fulfill({json:body});if(!p.startsWith('/api/'))return route.continue();
  if(p==='/api/auth/me')return json({id:1,username:'admin',role:'admin',is_active:true});
  if(p==='/api/training/catalog')return json(catalog);if(p==='/api/training/servers')return json([server]);if(p==='/api/projects')return json({items:[{id:'detect',project_name:'检测项目',task_type:'detection'},{id:'vector',project_name:'向量项目',task_type:'vector'}],total:2});
  if(p==='/api/export/projects/detect/snapshots')return json([{id:'detect-snapshot',approved:false,assets:[{split:'train',review_status:'approved'},{split:'val',review_status:'approved'}]},{id:'pending-snapshot',approved:true,assets:[{split:'train',review_status:'pending'},{split:'val',review_status:'approved'}]},{id:'nullstat-snapshot',approved:true,assets:[{split:'train',review_status:null},{split:'val',review_status:'approved'}]},{id:'missingr-snapshot',approved:false,assets:[{split:'train'},{split:'val',review_status:'approved'}]},{id:'missingv-snapshot',approved:true,assets:[{split:'train',review_status:'approved'}]},{id:'invalids-snapshot',approved:true,assets:[{split:'train'},{split:'val'},{split:'other'}]},{id:'legacyok-snapshot',approved:true,assets:[{split:'train'},{split:'val'}]}].map(x=>({id:x.id,format:'yolo',created_at:'2026-11-01T00:00:00Z',content_hash:'hash',manifest:{labels:['人','车'],approved_only:x.approved,assets:x.assets}})));
  if(p==='/api/export/projects/vector/snapshots')return json([{id:'vector-snapshot',format:'infonce',created_at:'2026-11-01T00:00:00Z',content_hash:'hash',manifest:{training:{source:'matches',samples:12}}}]);
  if(p==='/api/gpu-resources')return json({gpus:[]});if(p==='/api/training/defaults')return json({model_path:'/docker/model',output_root:'/docker/runs'});
  if(p==='/api/training/k8s/nodes')return new Promise(resolve=>setTimeout(()=>resolve(json({nodes:nodes.map(name=>({name,ready:true,gpu_capacity:8,gpu_available:8,taints:[]})),namespace:'aisfprod',pvc:'nfs-pvc-ai',nfs_root:root,output_root:root+'/runs',images:{detection:'k8s-detection:1',wemm:'k8s-wemm:1'},wemm_defaults:{model_path:root+'/model',dataset_path:'/adb-ssd-mount/ai/chen/train/wemm/dataset/20260918/wemm_embedding_train.jsonl',parameters:{learning_rate:0.00001},environment:{IMAGE_MAX_TOKEN_NUM:'1024'}}})),800));
  if(p==='/api/training/runs'&&req.method()==='GET')return json({items:run?[run]:[],total:run?1:0});
  if((p==='/api/training/runs'&&req.method()==='POST')||(p==='/api/training/runs/test'&&req.method()==='PUT')){const input=req.postDataJSON();writes.push(input);run={id:'test',name:input.name,kind:input.kind,status:'draft',config:input,source:input.snapshot_id?{kind:'snapshot',id:input.snapshot_id,project_id:input.snapshot_id==='vector-snapshot'?'vector':'detect',labels:input.snapshot_id==='vector-snapshot'?[]:['人','车'],sha256:'hash'}:{kind:'remote'},server:ks,output_dir:root+'/runs/test',inspection:{},created_at:new Date().toISOString()};return json(run);}
  if(p==='/api/training/runs/test/logs'){logs.push(u.searchParams.get('node'));return json({text:'logs'});}if(p==='/api/training/runs/test')return json(run);
  throw Error('Unexpected API '+req.method()+' '+p);
 });
 await page.goto('http://127.0.0.1:3108/training');
 const select=async(dialog,label,option)=>{await dialog.locator('.ant-form-item').filter({has:page.getByText(label,{exact:true})}).locator('.ant-select').click();await page.locator('.ant-select-dropdown:visible .ant-select-item-option').filter({hasText:option}).first().click();};
 for(const [kind,name] of Object.entries(names)){
  await page.getByRole('button',{name:'创建训练任务'}).click();const dialog=page.getByRole('dialog',{name:'创建训练任务'});await dialog.waitFor();
  await select(dialog,'运行环境','嘉善移动K8s');await dialog.getByLabel('训练数据来源').click();await page.locator('.ant-select-dropdown:visible .ant-select-item-option').filter({hasText:/平台(冻结导出集|标注数据)/}).waitFor({timeout:3000});await page.keyboard.press('Escape');await select(dialog,'训练器',name);
  await dialog.getByRole('textbox',{name:'任务名称'}).fill('测试 '+name);
  const ns=dialog.locator('.ant-form-item').filter({has:page.getByText('训练节点',{exact:true})}).locator('.ant-select');await ns.click();await page.locator('.ant-select-dropdown:visible .ant-select-item-option').filter({hasText:'node-a'}).click();await page.locator('.ant-select-dropdown:visible .ant-select-item-option').filter({hasText:'node-b'}).click();await page.keyboard.press('Escape');
  await dialog.getByRole('spinbutton',{name:'每节点GPU数'}).fill('2');await dialog.getByRole('textbox',{name:/服务器基础权重/}).fill(root+'/model');
  const data=dialog.locator('.ant-form-item').filter({has:dialog.locator('label').filter({hasText:/服务器.*(路径|目录)/}).first()});
  const dataset=dialog.locator('input[id="dataset_path"]');await dataset.fill(root+'/dataset');
  if(kind==='wemm'){await dialog.locator('input[id="parameters_learning_rate"]').fill('0.00003');await dialog.locator('input[id="output_subdir"]').fill('custom-output');await dialog.getByText('环境变量与分布式参数').click();await dialog.locator('input[id="environment_IMAGE_MAX_TOKEN_NUM"]').fill('512');}
  await dialog.getByRole('button',{name:'保存任务配置'}).click();await dialog.waitFor({state:'hidden'});
  const w=writes.at(-1);assert.equal(w.execution_backend,'k8s');assert.deepEqual(w.k8s,{nodes,gpus_per_node:2});assert.deepEqual(w.gpu_uuids,[]);assert.equal(w.server_id,undefined);assert.deepEqual(w.mounts,[]);assert.equal(w.kind,kind);assert.equal(w.image,kind==='wemm'?'k8s-wemm:1':'k8s-detection:1');assert.equal(w.output_root,root+'/runs');if(kind==='yolo26')assert.equal(w.parameters.device,'auto');if(kind.startsWith('rfdetr'))assert.equal(w.parameters.devices,'auto');
  if(kind==='wemm'){
   await page.getByRole('button',{name:'编辑配置'}).click();const edit=page.getByRole('dialog',{name:'编辑训练任务配置'});await edit.waitFor();assert.equal(await edit.getByRole('spinbutton',{name:'每节点GPU数'}).inputValue(),'2');await page.waitForTimeout(1000);await edit.getByRole('button',{name:'保存任务配置'}).click();await edit.waitFor({state:'hidden'});assert.deepEqual(writes.at(-1).k8s,{nodes,gpus_per_node:2});assert.equal(writes.at(-1).parameters.learning_rate,0.00003);assert.equal(writes.at(-1).environment.IMAGE_MAX_TOKEN_NUM,'512');assert.equal(writes.at(-1).output_subdir,'custom-output');
   await page.getByRole('button',{name:'复制配置新建任务'}).click();const copy=page.getByRole('dialog',{name:'创建训练任务'});await copy.waitFor();assert.equal(await copy.getByRole('spinbutton',{name:'每节点GPU数'}).inputValue(),'2');await copy.locator('input[id=image]').fill('');await page.waitForTimeout(1000);await copy.locator('input[id=image]').fill('k8s-wemm:1');await copy.getByRole('button',{name:'保存任务配置'}).click();await copy.waitFor({state:'hidden'});assert.deepEqual(writes.at(-1).k8s,{nodes,gpus_per_node:2});assert.equal(writes.at(-1).parameters.learning_rate,0.00003);assert.equal(writes.at(-1).environment.IMAGE_MAX_TOKEN_NUM,'512');assert.equal(writes.at(-1).output_subdir,'custom-output');
   await page.getByRole('tab',{name:'训练日志'}).click();await page.getByLabel('日志节点').click();await page.locator('.ant-select-dropdown:visible .ant-select-item-option').filter({hasText:'node-b'}).click();await page.waitForTimeout(300);assert.equal(logs.at(-1),'node-b');
   assert.equal(await page.getByRole('button',{name:'合并LoRA模型'}).count(),0);
  }
  await page.locator('.ant-drawer-close').click();
 }
 for(const [kind,name] of Object.entries(names)){
  await page.getByRole('button',{name:'创建训练任务'}).click();const dialog=page.getByRole('dialog',{name:'创建训练任务'});await dialog.waitFor();
  await dialog.getByLabel('训练数据来源').click();await page.locator('.ant-select-dropdown:visible .ant-select-item-option').filter({hasText:'平台冻结导出集'}).click();
  if(kind==='yolo26'){await select(dialog,'冻结导出所属项目','检测项目');await select(dialog,'冻结版本','detect-s');await dialog.getByText('全部已审核',{exact:false}).waitFor({timeout:3000});assert.ok(await dialog.getByText('训练集 1 · 验证集 1 · 测试集 0',{exact:false}).isVisible());await select(dialog,'冻结版本','pending-');await dialog.getByText('包含未审核或已驳回', {exact:false}).waitFor();await select(dialog,'冻结版本','nullstat');await dialog.getByText('包含未审核或已驳回', {exact:false}).waitFor();await select(dialog,'冻结版本','missingr');await dialog.getByText('包含未审核或已驳回', {exact:false}).waitFor();await select(dialog,'冻结版本','missingv');await dialog.getByText('缺少验证集', {exact:false}).waitFor();await select(dialog,'冻结版本','invalids');await dialog.getByText('存在无效划分', {exact:false}).waitFor();await select(dialog,'冻结版本','legacyok');await dialog.getByText('全部已审核',{exact:false}).waitFor();await select(dialog,'冻结版本','detect-s');}
  await select(dialog,'运行环境','嘉善移动K8s');assert.ok(await dialog.getByText('平台自动生成训练配置').isVisible());await select(dialog,'训练器',name);
  await dialog.getByRole('textbox',{name:'任务名称'}).fill('平台 '+name);
  const ns=dialog.locator('.ant-form-item').filter({has:page.getByText('训练节点',{exact:true})}).locator('.ant-select');await ns.click();await page.locator('.ant-select-dropdown:visible .ant-select-item-option').filter({hasText:'node-a'}).click();await page.keyboard.press('Escape');
  await dialog.getByRole('textbox',{name:/服务器基础权重/}).fill(root+'/model');
  if(kind==='wemm'){
    await dialog.getByLabel('平台标注项目').click();await page.locator('.ant-select-dropdown:visible .ant-select-item-option').filter({hasText:'向量项目'}).click();
    await dialog.getByLabel('平台数据冻结版本').click();await page.locator('.ant-select-dropdown:visible .ant-select-item-option').filter({hasText:'infonce'}).click();
  }else{
    if(kind!=='yolo26'){await select(dialog,'冻结导出所属项目','检测项目');await select(dialog,'冻结版本','detect-s');}
    if(kind==='yolo26'){await select(dialog,'冻结版本','pending-');const prior=writes.length;await dialog.getByRole('button',{name:'保存任务配置'}).click();await dialog.getByText('冻结版本不满足训练要求', {exact:false}).waitFor();assert.equal(writes.length,prior);await select(dialog,'冻结版本','detect-s');}
    const labels=dialog.locator('.ant-form-item').filter({has:page.getByText('训练标签',{exact:true})}).locator('.ant-select');await labels.click();await page.locator('.ant-select-dropdown:visible .ant-select-item-option').filter({hasText:'车'}).click();await page.keyboard.press('Escape');
  }
  await dialog.getByRole('button',{name:'保存任务配置'}).click();await dialog.waitFor({state:'hidden'});
  const expected=kind==='wemm'?'vector-snapshot':'detect-snapshot';let w=writes.at(-1);assert.equal(w.snapshot_id,expected);assert.equal(w.dataset_path,'');if(kind!=='wemm')assert.deepEqual(w.selected_labels,['人']);
  for(const [action,title] of [['编辑配置','编辑训练任务配置'],['复制配置新建任务','创建训练任务']]){
   await page.getByRole('button',{name:action}).click();const restored=page.getByRole('dialog',{name:title});await restored.waitFor();await page.waitForTimeout(1000);
   assert.ok(await restored.getByText(kind==='wemm'?'冻结平台标注用于训练':'平台自动生成训练配置').isVisible());await restored.getByRole('button',{name:'保存任务配置'}).click();await restored.waitFor({state:'hidden'});
   w=writes.at(-1);assert.equal(w.snapshot_id,expected);assert.equal(w.dataset_path,'');if(kind!=='wemm')assert.deepEqual(w.selected_labels,['人']);
  }
  await page.locator('.ant-drawer-close').click();
 }
 assert.deepEqual(errors,[]);console.log('K8s four trainers: remote and platform sources, edit, clone and node logs passed');
 }finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1});
