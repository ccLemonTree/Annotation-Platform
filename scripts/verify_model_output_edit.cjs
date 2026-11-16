module.exports=async(page,origin)=>{
  const parentId='11111111-1111-4111-8111-111111111111',jobId='22222222-2222-4222-8222-222222222222';
  const server={id:'default',name:'训练服务器',enabled:true};
  const parent={id:parentId,name:'测试训练',kind:'rfdetr_medium',status:'completed',server,
    config:{gpu_uuids:['GPU-source'],model_path:'/data/base.pth',image:'train:1',output_subdir:'train',parameters:{}},
    source:{kind:'remote'},output_dir:'/data/runs/'+parentId,inspection:{},created_at:'2026-10-28T00:00:00Z'};
  const output={format:'tensorrt',server_id:'default',gpu_uuid:'GPU-target',source_gpu_uuid:'GPU-source',
    source_path:'/data/exports/'+jobId+'/onnx/model.onnx',output_root:'/data/exports',image:'target:1',onnx_image:'source:1',
    batch:1,dynamic_batch:false,min_batch:1,opt_batch:1,max_batch:8,fp16:true,opset:13,simplify:true,
    input_name:'input',output_name:'output',trust_checkpoint:false,model_type:'',torch_dtype:'float16'};
  let job={id:jobId,name:'测试训练 · TENSORRT',kind:'model_export',status:'draft',server,
    config:{kind:'model_export',gpu_uuids:['GPU-target'],output,onnx_stage:{id:'stage',server,config:{}},output_subdir:'result'},
    source:{kind:'model_output',run_id:parentId,source_path:'/data/checkpoint.pth'},
    output_dir:'/data/exports/'+jobId,inspection:{ok:true},created_at:'2026-10-29T00:00:00Z'};
  await page.addInitScript(()=>{localStorage.setItem('user',JSON.stringify({role:'admin'}));localStorage.setItem('token','fixture-token');});
  let written;
  await page.route('**/api/**',route=>{
    const path=new URL(route.request().url()).pathname;
    if(!path.startsWith('/api/'))return route.continue();
    const json=body=>route.fulfill({json:body});
    if(path==='/api/auth/me')return json({id:1,username:'admin',role:'admin',is_active:true});
    if(path==='/api/training/catalog')return json({});
    if(path==='/api/training/servers')return json([server]);
    if(path==='/api/projects')return json({items:[],total:0});
    if(path==='/api/training/runs')return json({items:[job],total:1});
    if(path==='/api/training/runs/'+parentId)return json(parent);
    if(path==='/api/training/runs/'+parentId+'/artifacts')return json({files:[],truncated:false});
    if(path==='/api/gpu-resources')return json({gpus:[{index:0,uuid:'GPU-source',name:'Source'},{index:1,uuid:'GPU-target',name:'Target'}]});
    if(path==='/api/training/runs/'+jobId+'/outputs'&&route.request().method()==='PUT'){
      written=route.request().postDataJSON();job={...job,inspection:{},config:{...job.config,output:written}};return json(job);
    }
    throw Error('Unexpected API: '+route.request().method()+' '+path);
  });
  await page.goto(origin+'/training',{waitUntil:'domcontentloaded'});
  await page.getByRole('button',{name:'查看详情'}).click();
  const edit=page.getByRole('button',{name:'编辑配置'});
  if(await edit.isDisabled())throw Error('Draft output edit is disabled');
  await edit.click();
  const dialog=page.getByRole('dialog',{name:'编辑导出 / 合并配置'});
  await dialog.waitFor();
  const source=dialog.getByRole('textbox',{name:/训练服务器检查点路径/});
  if(await source.inputValue()!=='/data/checkpoint.pth')throw Error('Original checkpoint path was not restored');
  if(await dialog.getByRole('textbox',{name:'训练服务器ONNX导出镜像'}).inputValue()!=='source:1')throw Error('ONNX image was not restored');
  await dialog.getByRole('textbox',{name:'训练服务器ONNX导出镜像'}).fill('source:2');
  await dialog.getByRole('button',{name:'保存任务配置'}).click();
  await dialog.waitFor({state:'hidden'});
  if(!written||written.onnx_image!=='source:2'||written.source_path!=='/data/checkpoint.pth')throw Error('Edited output payload is incorrect');
  if(!await page.getByRole('button',{name:'启动执行'}).isDisabled())throw Error('Old precheck was not cleared');
  return 'Draft output edit restored values, saved same job, and invalidated precheck';
};
