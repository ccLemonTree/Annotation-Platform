async (page) => {
  page.setDefaultTimeout(8000);
  const checks=[],writes=[];
  const sourceCode=await(await page.request.get('http://127.0.0.1:3000/src/components/ImageIngestSettings.tsx')).text();
  const reactUrl=sourceCode.match(/["'](\/node_modules\/\.vite\/deps\/react\.js\?[^"']+)["']/)[1];
  const config={classification_enabled:true,base_url:'http://manual.test/v1',model:'manual-model',scenes:['工厂','电梯'],min_long_edge:720,scene_prompt:'按可见内容分类。'};
  let unavailable=false;
  const app='**/src/App.tsx*',api='**/api/**';
  await page.route(app,route=>route.fulfill({contentType:'application/javascript',body:`
    import React from '${reactUrl}';
    import Provider from '/src/ui/PlatformProvider.tsx';
    import ImageIngestSettings from '/src/components/ImageIngestSettings.tsx';
    export default function Harness(){return React.createElement(Provider,null,React.createElement(ImageIngestSettings,{collectionId:'fixture',collectionName:'测试图片集'}));}
  `}));
  await page.route(api,route=>{
    const req=route.request(),path=new URL(req.url()).pathname;
    if(!path.startsWith('/api/'))return route.continue();
    if(path==='/api/model-services/prompt-defaults')return route.fulfill({json:{caption:'描述可见内容。',review:'逐框审核。',scene:'按可见内容分类。'}});
    if(path==='/api/model-services')return route.fulfill(unavailable?{status:404,json:{detail:'not installed'}}:{json:[
      {id:'caption',name:'Qwen 描述',enabled:true,protocol:'openai_chat',kind:'caption',base_url:'http://qwen.test/v1',model_name:'/models/qwen'},
      {id:'off',name:'已停用描述',enabled:false,protocol:'openai_chat',kind:'caption',base_url:'http://off.test/v1',model_name:'off'},
      {id:'embedding',name:'Embedding',enabled:true,protocol:'openai_embedding',kind:'embedding',base_url:'http://embedding.test/v1',model_name:'/models/wemm_embedding_2b'},
      {id:'custom',name:'自定义鉴权',enabled:true,protocol:'openai_chat',kind:'caption',base_url:'http://custom.test/v1',model_name:'custom',api_key_env:'MODEL_API_KEY_CUSTOM'},
    ]});
    if(path==='/api/images/ingest-settings'&&req.method()==='GET')return route.fulfill({json:config});
    if(path==='/api/images/ingest-settings'&&req.method()==='PUT'){
      writes.push(req.postDataJSON());return route.fulfill({json:req.postDataJSON()});
    }
    throw Error('Unexpected API: '+path);
  });
  try{
    await page.goto('http://127.0.0.1:3000');
    await page.getByRole('button',{name:'图片集设置',exact:true}).click();
    await page.getByRole('combobox',{name:'复制模型管理配置',exact:true}).click();
    const unsupported=page.locator('.ant-select-item-option').filter({hasText:'自定义鉴权（暂不支持复制鉴权）'});
    if(!((await unsupported.getAttribute('class'))||'').includes('disabled'))throw Error('Unsupported auth can be copied');
    if(await page.getByText('已停用描述',{exact:true}).count()||await page.getByText('Embedding',{exact:true}).count())throw Error('Incompatible model included in copy choices');
    await page.getByText('Qwen 描述',{exact:true}).click();
    if(await page.getByRole('textbox',{name:'模型服务地址',exact:true}).inputValue()!=='http://qwen.test/v1'
      ||await page.getByRole('textbox',{name:'场景模型名称',exact:true}).inputValue()!=='/models/qwen')throw Error('Copy failed');
    await page.getByRole('textbox',{name:'场景模型名称',exact:true}).fill('manual-after-copy');
    await page.getByRole('button',{name:'保存设置',exact:true}).click();
    await page.getByRole('dialog').waitFor({state:'hidden'});
    const saved=writes[0];
    if(saved.model!=='manual-after-copy'||saved.base_url!=='http://qwen.test/v1'||saved.min_long_edge!==720||saved.scenes.join()!==config.scenes.join()||'model_service_id' in saved)throw Error('Copy changed collection rules or leaked selection field');
    checks.push('copy only enabled chat address/model; manual editing and collection rules preserved; no model_service_id sent');
    unavailable=true;
    await page.getByRole('button',{name:'图片集设置',exact:true}).click();
    await page.getByText(/可继续手动配置/).waitFor();
    await page.getByRole('textbox',{name:'场景模型名称',exact:true}).fill('safe-fallback');
    await page.getByRole('button',{name:'保存设置',exact:true}).click();await page.getByRole('dialog').waitFor({state:'hidden'});
    if(writes.at(-1).model!=='safe-fallback')throw Error('Fallback blocked save');
    checks.push('model-list 404 safely falls back to existing manual settings');
    return {checks,writesIntercepted:writes.length};
  }finally{await page.unroute(api);await page.unroute(app);}
}
