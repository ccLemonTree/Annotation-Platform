const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require(path.join(process.env.USERPROFILE,'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));

async function verify(page,origin){
  const errors=[],writes=[];page.on('pageerror',error=>{errors.push(error.message);console.error(error.message);});
  page.on('console',message=>{if(message.type()==='error')console.error(message.text());});
  await page.route('**/api/**',route=>{
    const request=route.request(),url=new URL(request.url());
    if(!url.pathname.startsWith('/api/'))return route.continue();
    if(request.method()!=='GET'){writes.push(url.pathname);return route.abort();}
    if(url.pathname==='/api/gpu-resources'||url.pathname.endsWith('/resources'))return route.fulfill({json:{gpus:[{index:0,name:'Tesla T4',uuid:'GPU-t4'},{index:1,name:'NVIDIA A10',uuid:'GPU-a10'}]}});
    if(url.pathname.endsWith('/artifacts'))return route.fulfill({json:{files:[{path:'checkpoint_best.pth',bytes:10}],truncated:false}});
    return route.fulfill({json:{}});
  });
  await page.goto(origin+'/login');
  await page.evaluate(async()=>{
    const main=await (await fetch('/src/main.tsx')).text(),version=main.match(/react-dom_client\.js\?v=([^"']+)/)[1];
    const [{default:React},{default:ReactDOM},{Select,AutoComplete,Input},{default:Provider},{default:ModelOutputForm}]=await Promise.all([
      import('/node_modules/.vite/deps/react.js?v='+version),import('/node_modules/.vite/deps/react-dom_client.js?v='+version),import('/node_modules/.vite/deps/antd.js?v='+version),
      import('/src/ui/PlatformProvider.tsx'),import('/src/components/ModelOutputForm.tsx')]);
    const h=React.createElement,servers=[{id:'server-a',name:'台州2',enabled:true},{id:'server-b',name:'绍兴A100',enabled:true},{id:'server-c',name:'停用服务器',enabled:false}];
    const run={id:'fixture',kind:'rfdetr_medium',server:servers[1],config:{gpu_uuids:['GPU-a10'],parameters:{}},source:{},output_dir:'/fixture'};
    const job={source:{},config:{output:{format:'tensorrt',server_id:'server-a',gpu_uuid:'GPU-t4',source_gpu_uuid:'GPU-a10',source_path:'/fixture/checkpoint_best.pth'}}};
    window.searchSelections={};
    const select=(name,props)=>h(Select,{'aria-label':name,style:{width:300},onChange:value=>window.searchSelections[name]=value,...props});
    const root=document.createElement('div');document.body.replaceChildren(root);
    ReactDOM.createRoot(root).render(h(Provider,null,h('div',{style:{padding:24}},
      h(ModelOutputForm,{run,job,servers,admin:true,onCreated:()=>{throw Error('Must not create a real task');}}),
      h('div',{style:{display:'flex',gap:16,flexWrap:'wrap'}},
        select('普通单选',{options:[{value:'id-one',label:'目标平台 Alpha'},{value:'id-two',label:'其他 Beta'}],allowClear:true}),
        select('分组多选',{mode:'multiple',options:[{label:'生产环境',options:[{value:'id-one',label:'目标平台 Alpha'},{value:'id-two',label:'其他 Beta'}]}]}),
        select('标签输入',{mode:'tags',options:[{value:'label-one',label:'目标标签'},{value:'label-two',label:'其他标签'}]}),
        select('旧版子选项',{children:[h(Select.Option,{key:'image',value:'image'},'图片数据'),h(Select.Option,{key:'video',value:'video'},'视频数据')]}),
        select('已有名称搜索',{showSearch:true,optionFilterProp:'label',options:[{value:'uuid-one',label:'已有目标'},{value:'uuid-two',label:'其他选项'}]}),
        select('远程搜索',{showSearch:true,filterOption:false,onSearch:value=>{if(value)window.searchSelections.remoteQuery=value;},options:[{value:'remote-id',label:'服务端返回的候选'}]}),
        select('自定义搜索',{showSearch:{filterOption:(input,option)=>option.searchText.includes(input.toLowerCase())},options:[{value:'server-id',label:'中转服务器',searchText:'192.168.1.115'}]}),
        h(AutoComplete,{'aria-label':'图片集补全',options:[{value:'既有图片集'}],filterOption:(input,option)=>option.value.includes(input),style:{width:300}},h(Input,{'aria-label':'图片集补全'}))
      ))));
  });
  const matched=[];
  await page.waitForFunction(()=>document.querySelectorAll('[role="combobox"]').length>0);
  async function search(name,query,expected,excluded){
    const input=page.getByRole('combobox',{name});await input.click();
    await input.fill(query);
    const listId=await input.getAttribute('aria-controls');
    const popup=page.locator('.ant-select-dropdown').filter({has:page.locator(`[id="${listId}"]`)});
    await popup.getByText(expected,{exact:true}).last().waitFor();
    if(excluded)await popup.getByText(excluded,{exact:true}).waitFor({state:'hidden'});
    matched.push(name);return {input,popup};
  }
  // The screenshot's real form: searchable names differ from server IDs and GPU UUIDs.
  let found=await search('TensorRT目标GPU服务器','台州','台州2','绍兴A100');
  await found.popup.getByText('台州2',{exact:true}).click();
  found=await search('TensorRT构建GPU','t4','0 · Tesla T4 · GPU-t4','1 · NVIDIA A10 · GPU-a10');await found.input.press('Escape');
  found=await search('ONNX导出GPU（训练服务器：绍兴A100）','a10','1 · NVIDIA A10 · GPU-a10','0 · Tesla T4 · GPU-t4');await found.input.press('Escape');
  found=await search('输出格式','tensor','TensorRT（先导出ONNX）','ONNX');await found.input.press('Escape');
  for(const [name,query,expected,excluded] of [
    ['普通单选','aLpH','目标平台 Alpha','其他 Beta'],['分组多选','目标','目标平台 Alpha','其他 Beta'],
    ['标签输入','目标','目标标签','其他标签'],['旧版子选项','图片','图片数据','视频数据'],
    ['已有名称搜索','已有','已有目标','其他选项'],['远程搜索','与候选不同的查询','服务端返回的候选'],
    ['自定义搜索','1.115','中转服务器'],['图片集补全','既有','既有图片集']]){
    found=await search(name,query,expected,excluded);
    if(['普通单选','分组多选','标签输入'].includes(name))await found.popup.getByText(expected,{exact:true}).click();
    await found.input.press('Escape');
  }
  assert.deepEqual(await page.evaluate(()=>window.searchSelections),{'普通单选':'id-one','分组多选':['id-one'],'标签输入':['label-one'],remoteQuery:'与候选不同的查询'});
  const input=page.getByRole('combobox',{name:'普通单选',exact:true});await input.click();await input.fill('不存在的选项');
  await page.locator('.ant-select-dropdown:visible .ant-empty').waitFor();await input.fill('');
  await page.locator('.ant-select-dropdown:visible').getByText('其他 Beta',{exact:true}).waitFor();await input.press('Escape');
  await page.setViewportSize({width:390,height:844});
  found=await search('TensorRT目标GPU服务器','台州','台州2','绍兴A100');
  await page.screenshot({path:'output/playwright/select-search-mobile.png',fullPage:false});
  assert.deepEqual(writes,[]);assert.deepEqual(errors,[]);
  return {passed:true,matched,case_insensitive:true,grouped_and_multiple:true,remote_search_preserved:true,custom_filter_preserved:true,empty_query_restores_options:true,mobile:true,business_writes:0,errors};
}

(async()=>{fs.mkdirSync('output/playwright',{recursive:true});const browser=await chromium.launch({channel:'msedge',headless:true});try{
  const page=await browser.newPage({viewport:{width:1280,height:900},serviceWorkers:'block'});page.setDefaultTimeout(8000);
  const result=await verify(page,process.argv[2]||'http://127.0.0.1:3108');
  fs.writeFileSync('output/playwright/select-search-verification.json',JSON.stringify(result,null,2));console.log(JSON.stringify(result));
}finally{await browser.close();}})().catch(error=>{console.error(error);process.exitCode=1;});
