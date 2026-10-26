async (page) => {
  page.setDefaultTimeout(10000);
  await page.setViewportSize({width:1700,height:950});
  const checks = [], writes = [];
  const requests = [];
  const recordRequest = req => { if(new URL(req.url()).pathname.startsWith('/api/'))requests.push(req.method()+' '+new URL(req.url()).pathname); };
  page.on('request',recordRequest);
  const sourceCode = await (await page.request.get('http://127.0.0.1:3000/src/components/ModelPrediction.tsx')).text();
  const reactUrl = sourceCode.match(/["'](\/node_modules\/\.vite\/deps\/react\.js\?[^"']+)["']/)[1];
  const managementSource = await (await page.request.get('http://127.0.0.1:3000/src/pages/ModelServicesPage.tsx')).text();
  const routerUrl = managementSource.match(/["'](\/node_modules\/\.vite\/deps\/react-router-dom\.js\?[^"']+)["']/)[1];
  const asset = 'aaaaaaaa-0000-4000-8000-000000000001';
  const image = 'bbbbbbbb-0000-4000-8000-000000000001';
  const base = { base_url: 'http://model.test', endpoint: '/detect', protocol: 'model_service', kind: 'detection', model_name: '', enabled: true,
    timeout_seconds: 60, confidence: .25, iou: .45, labels: [], label_mapping: {}, api_key_env: '', revision: 1 };
  let models = [
    { ...base, endpoint:'/detect/grounding', id: '11111111-1111-4111-8111-111111111111', key: 'detector', name: '检测模型' },
    { ...base, id: '22222222-2222-4222-8222-222222222222', key: 'caption', name: '描述模型', kind: 'caption', protocol: 'openai_chat' },
    { ...base, id: '33333333-3333-4333-8333-333333333333', key: 'embedding', name: '向量模型', kind: 'embedding', protocol: 'openai_embedding' },
    { ...base, id: '55555555-5555-4555-8555-555555555555', key: 'reranker', name: '重排序配置', kind: 'reranker', protocol: 'openai_rerank' },
  ];
  let role = 'admin', prediction = 'boxes', release, delayed, originalStorage;
  const modelPattern = '**/api/**', appPattern = '**/src/App.tsx*';
  await page.route(appPattern, route => route.fulfill({ contentType: 'application/javascript', body: `
    import React from '${reactUrl}';
    import { BrowserRouter } from '${routerUrl}';
    const {useState} = React;
    import Provider from '/src/ui/PlatformProvider.tsx';
    import ModelServicesPage from '/src/pages/ModelServicesPage.tsx';
    import ModelPrediction from '/src/components/ModelPrediction.tsx';
    import { draftHistory, changeDraft, undoDraft } from '/src/components/detectionEditor.ts';
    export default function Harness() {
      const [id, setId] = useState('${asset}'), [disabled, setDisabled] = useState(false);
      const [draft, setDraft] = useState(() => draftHistory({boxes:[{label:'fire',x:1,y:1,width:2,height:2}],negative:false}));
      return React.createElement(BrowserRouter, null, React.createElement(Provider, null, location.search.includes('workspace')
        ? React.createElement('div', {style:{width:320,padding:16}},
            React.createElement('button', {onClick:()=>setId(id === '${asset}' ? '${image}' : '${asset}')}, '切换图片'),
            React.createElement('button', {onClick:()=>setDisabled(!disabled)}, '切换历史只读'),
            React.createElement('button', {onClick:()=>setDraft(undoDraft)}, '撤销应用'),
            React.createElement('output', {'aria-label':'草稿'}, JSON.stringify(draft.present)),
            React.createElement(ModelPrediction, {assetId:id, labels:['fire'], disabled,
              onApply:boxes=>setDraft(previous=>changeDraft(previous,{boxes,negative:false}))}))
        : React.createElement(ModelServicesPage)));
    }` }));
  await page.route(modelPattern, async route => {
    const req = route.request(), url = new URL(req.url()), path = url.pathname, method = req.method();
    if (!path.startsWith('/api/')) return route.continue();
    const payload = method === 'GET' || method === 'DELETE' ? null : req.postDataJSON();
    if (path === '/api/auth/me') return route.fulfill({json:{id:1,username:'fixture',role}});
    if (path === '/api/images/collections') return route.fulfill({json:[]});
    if (path === '/api/images') return route.fulfill({json:{items:[{id:image,original_name:'试运行图片.png',width:640,height:480}],total:1,page:1,page_size:20}});
    if (path.endsWith('/content')) return route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="640" height="480"><rect width="640" height="480" fill="#e2e8f0"/></svg>'});
    if (!path.startsWith('/api/model-services')) throw Error('Unexpected API: '+method+' '+path);
    if (method === 'GET') return route.fulfill({json:models.filter(m => (!url.searchParams.has('enabled') || m.enabled === (url.searchParams.get('enabled') === 'true')) && (!url.searchParams.has('kind') || m.kind === url.searchParams.get('kind')))});
    writes.push({method,path,payload});
    if (path.endsWith('/defaults')) return route.fulfill({json:models});
    const id = path.split('/')[3], model = models.find(m=>m.id===id);
    if (path.endsWith('/test')) return route.fulfill({json:{ok:true,detail:'连接成功',models:['fixture-model']}});
    if (path.endsWith('/predict')) {
      if (prediction === 'delayed') { delayed = new Promise(resolve=>{release=resolve;}); await delayed; }
      const kind = model.kind;
      return route.fulfill({json:{kind,model_service_id:id,config_revision:1,warnings:['测试预览警告'],
        boxes:kind==='detection' && prediction!=='empty' ? [{label:'smoke',x:30,y:40,width:70,height:80,score:.9}] : [],
        ...(kind==='caption'?{caption:'这是一张测试图片'}:{}),
        ...(kind==='embedding'?{embedding_dimensions:1024,embedding_preview:Array.from({length:32},(_,i)=>i+.125)}:{})}});
    }
    if (method === 'POST') { const created={...base,...payload,id:'44444444-4444-4444-8444-444444444444'}; models.push(created); return route.fulfill({json:created}); }
    if (method === 'PUT') { Object.assign(model,payload,{revision:model.revision+1}); return route.fulfill({json:model}); }
    if (method === 'DELETE') { models=models.filter(m=>m.id!==id); return route.fulfill({status:204}); }
    throw Error('Unexpected model API');
  });
  const goto = async workspace => {
    await page.goto('http://127.0.0.1:3000/'+(workspace?'?workspace':''));
    if(!originalStorage)originalStorage=await page.evaluate(()=>({token:localStorage.getItem('token'),user:localStorage.getItem('user')}));
    await page.evaluate(role => {localStorage.setItem('token','model-services-fixture');localStorage.setItem('user',JSON.stringify({id:1,username:'fixture',role}));},role);
    await page.reload();
  };
  const choose = async (name, option) => {
    await page.getByRole('combobox',{name,exact:true}).click();
    await page.locator('.ant-select-dropdown:visible .ant-select-item-option-content').filter({hasText:option}).click();
  };
  try {
    await goto(false);
    await page.getByRole('button',{name:'添加模型',exact:true}).waitFor();
    for(const title of ['ID','KEY','名称','URL','ENDPOINT','类型','状态','操作']) await page.getByRole('columnheader',{name:title,exact:true}).waitFor();
    const rerankerTrial=page.getByRole('row').filter({hasText:'重排序配置'}).getByRole('button',{name:'试运行',exact:true});
    if(await rerankerTrial.isEnabled())throw Error('Configuration-only reranker can run inference');
    await rerankerTrial.locator('..').hover();
    await page.getByRole('tooltip',{name:'仅保存配置，部署后接入推理',exact:true}).waitFor();
    checks.push('management table follows required columns');
    await page.getByRole('button',{name:'添加模型',exact:true}).click();
    await choose('模型类型','重排序');
    if(await page.getByRole('checkbox',{name:'启用模型',exact:true}).isChecked())throw Error('New reranker enabled by default');
    await choose('模型类型','目标检测');
    await page.getByRole('checkbox',{name:'启用模型',exact:true}).check();
    await page.getByRole('textbox',{name:'KEY',exact:true}).fill('new-model');
    await page.getByRole('textbox',{name:'名称',exact:true}).fill('新模型');
    await page.getByRole('textbox',{name:'URL',exact:true}).fill('http://model.test');
    await page.getByRole('textbox',{name:'ENDPOINT',exact:true}).fill('/detect');
    await page.getByRole('button',{name:'保存模型',exact:true}).click();
    await page.getByRole('dialog').waitFor({state:'hidden'});
    const created = writes.find(w=>w.method==='POST'&&w.path==='/api/model-services').payload;
    if('prompts' in created || 'id' in created || 'revision' in created || 'label_mapping_text' in created)throw Error('Extra config fields would be rejected');
    if(!created.enabled || created.timeout_seconds!==60 || created.confidence!==.25 || created.iou!==.45 || created.labels.length || Object.keys(created.label_mapping).length) throw Error('Creation defaults differ');
    checks.push('creation sends required defaults');
    let row = page.getByRole('row').filter({hasText:'new-model'});
    await row.getByRole('button',{name:'编辑',exact:true}).click();
    await page.getByRole('textbox',{name:'名称',exact:true}).fill('新模型已改');
    await page.getByRole('button',{name:'保存模型',exact:true}).click();
    await page.getByRole('dialog').waitFor({state:'hidden'});
    await row.getByRole('button',{name:'停用',exact:true}).click();
    await row.getByRole('button',{name:'启用',exact:true}).waitFor();
    await row.getByRole('button',{name:'启用',exact:true}).click();
    await row.getByRole('button',{name:'连接测试',exact:true}).click();
    await page.getByText('连接成功',{exact:true}).waitFor();
    await page.getByRole('button',{name:'关闭',exact:true}).last().click();
    await page.getByRole('button',{name:'添加默认模型',exact:true}).click();
    await page.getByText('不会覆盖已有配置',{exact:false}).first().waitFor();
    await page.getByRole('button',{name:'确认添加',exact:true}).click();
    await row.getByRole('button',{name:'删除',exact:true}).click();
    await page.getByRole('button',{name:'确认删除',exact:true}).click();
    await row.waitFor({state:'hidden'});
    checks.push('edit, enable/disable, test, defaults and delete use contract');
    row = page.getByRole('row').filter({hasText:'向量模型'});
    await row.getByRole('button',{name:'试运行',exact:true}).click();
    await page.getByRole('textbox',{name:'图片 ID',exact:true}).fill(image);
    await page.getByRole('button',{name:'开始试运行',exact:true}).click();
    await page.getByText('1024',{exact:true}).waitFor();
    if(await page.getByRole('dialog').getByText('31.125',{exact:false}).count()) throw Error('Embedding preview leaked long vector');
    if(writes.at(-1).payload.image_id!==image || 'asset_id' in writes.at(-1).payload) throw Error('Predict must send exactly one image parameter');
    await page.getByRole('button',{name:'关闭',exact:true}).last().click();
    await page.getByRole('dialog').waitFor({state:'hidden'});
    checks.push('library-ID trial and bounded embedding preview');
    await page.screenshot({path:'output/playwright/model-services.png'});
    role='user'; await goto(false);
    if(await page.getByRole('button',{name:'添加模型',exact:true}).count() || await page.getByRole('button',{name:'连接测试',exact:true}).count()) throw Error('Ordinary user sees admin actions');
    await page.getByRole('button',{name:'试运行',exact:true}).first().waitFor();
    checks.push('ordinary user can preview and cannot manage');
    await goto(true);
    await choose('预测模型','检测模型');
    if(!await page.getByRole('textbox',{name:'预测提示词',exact:true}).isEnabled())throw Error('Grounding detection prompt is disabled');
    await page.getByRole('textbox',{name:'预测提示词',exact:true}).fill('electric bicycle');
    await page.getByRole('button',{name:'预测预览',exact:true}).click();
    const preview = page.getByRole('dialog',{name:'预测结果预览',exact:true});
    const apply = preview.getByRole('button',{name:'应用到草稿',exact:true});
    await preview.waitFor();
    if(writes.at(-1).payload.prompt!=='electric bicycle')throw Error('Detection prompt was not forwarded');
    await preview.getByLabel('预测框预览',{exact:true}).waitFor();
    if(await apply.isEnabled()) throw Error('Unknown category accepted without mapping');
    await choose('映射 smoke','fire');
    await page.waitForFunction(()=>[...document.querySelectorAll('button')].some(button=>button.textContent==='应用到草稿'&&!button.disabled));
    await page.locator('.ant-select-dropdown:visible').waitFor({state:'hidden'});
    await preview.locator('.ant-modal-body').evaluate(element=>{element.scrollTop=0;});
    await page.screenshot({path:'output/playwright/model-prediction.png'});
    await preview.getByText(/替换当前草稿中的全部框/).waitFor();
    await apply.click();
    let draft=JSON.parse(await page.getByLabel('草稿',{exact:true}).textContent());
    if(draft.boxes[0].label!=='fire'||draft.boxes[0].x!==30||draft.negative)throw Error('Mapping or draft apply incorrect');
    await page.getByRole('button',{name:'撤销应用',exact:true}).click();
    draft=JSON.parse(await page.getByLabel('草稿',{exact:true}).textContent());
    if(draft.boxes[0].x!==1)throw Error('Cannot undo applied preview');
    checks.push('explicit unknown-label mapping, replacement warning, undoable draft');
    prediction='empty'; await page.getByRole('button',{name:'预测预览',exact:true}).click(); await preview.waitFor();
    await preview.getByText(/不能自动认定为负样本/).waitFor();
    if(await apply.isEnabled())throw Error('Zero boxes can overwrite draft');
    await preview.getByRole('button',{name:'关闭',exact:true}).last().click();
    checks.push('zero boxes do not create negative sample or replace draft');
    prediction='delayed';await page.getByRole('button',{name:'预测预览',exact:true}).click();
    await page.waitForFunction(()=>document.querySelector('button.ant-btn-loading'));
    await page.getByRole('button',{name:'切换图片',exact:true}).click();
    while(!release)await new Promise(resolve=>setTimeout(resolve,20));
    release(); await page.waitForLoadState('networkidle');
    if(await preview.isVisible())throw Error('Late prediction displayed for next asset');
    checks.push('asset switch discards late prediction');
    await page.getByRole('button',{name:'切换历史只读',exact:true}).click();
    if(await page.getByRole('button',{name:'预测预览',exact:true}).isEnabled())throw Error('History prediction enabled');
    checks.push('history/disabled blocks prediction');
    if(writes.some(w=>w.path.includes('annotations')))throw Error('Preview wrote annotations');
    await page.screenshot({path:'output/playwright/model-prediction-disabled.png'});
    return {checks,writesIntercepted:writes.length,annotationWrites:0};
  } catch (error) {
    const buttons=await page.getByRole('dialog').getByRole('button').evaluateAll(nodes=>nodes.map(node=>({text:node.textContent,disabled:node.disabled})));
    throw new Error(error.message + '\nCompleted: ' + checks.join('; ') + '\nRequests: '+requests.join('; ')+'\nDialog buttons: '+JSON.stringify(buttons));
  } finally {
    release?.(); page.off('request',recordRequest);
    if(originalStorage)await page.evaluate(storage=>{for(const [key,value] of Object.entries(storage)){if(value===null)localStorage.removeItem(key);else localStorage.setItem(key,value);}},originalStorage);
    await page.unroute(modelPattern); await page.unroute(appPattern);
  }
}
