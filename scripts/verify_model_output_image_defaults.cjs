module.exports=async(page,origin)=>{
  const run={id:'11111111-1111-4111-8111-111111111111',kind:'rfdetr_medium',server:{id:'default',name:'训练服务器'},
    config:{gpu_uuids:['GPU-test'],model_path:'',image:'training:latest',output_subdir:'result',parameters:{}},output_dir:'/ai/chen/training/run'};
  const app=`import React from '/node_modules/.vite/deps/react.js';
    import ModelOutputForm from '/src/components/ModelOutputForm.tsx';
    export default function App(){return React.createElement(ModelOutputForm,{run:${JSON.stringify(run)},servers:[{id:'default',name:'训练服务器',enabled:true}],admin:true,onCreated:()=>{}})}`;
  await page.route('**/src/App.tsx*',route=>route.fulfill({contentType:'text/javascript',body:app}));
  await page.route('**/api/**',route=>{
    const path=new URL(route.request().url()).pathname;
    if(!path.startsWith('/api/'))return route.continue();
    if(path.endsWith('/artifacts'))return route.fulfill({json:{files:[],truncated:false}});
    if(path==='/api/gpu-resources')return route.fulfill({json:{gpus:[{index:0,uuid:'GPU-test',name:'Test GPU'}]}});
    throw Error('Unexpected API request: '+path);
  });
  await page.goto(origin+'/',{waitUntil:'domcontentloaded'});
  await page.getByRole('combobox',{name:'输出格式'}).click();
  await page.getByText('TensorRT（先导出ONNX）',{exact:true}).last().click();
  const onnx=page.getByRole('textbox',{name:'训练服务器ONNX导出镜像'});
  const target=page.getByRole('textbox',{name:'目标服务器TensorRT构建镜像'});
  await onnx.fill('registry.example/onnx:new');
  await target.fill('registry.example/tensorrt:new');
  await page.reload({waitUntil:'domcontentloaded'});
  await page.getByRole('combobox',{name:'输出格式'}).click();
  await page.getByText('TensorRT（先导出ONNX）',{exact:true}).last().click();
  if(await onnx.inputValue()!=='registry.example/onnx:new'||await target.inputValue()!=='registry.example/tensorrt:new')
    throw Error('Edited image addresses were lost after reopening');
  return 'Both image addresses survive reopening';
};
