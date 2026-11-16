async page=>{
  await page.unrouteAll({behavior:'ignoreErrors'});page.setDefaultTimeout(10000);
  const base='http://127.0.0.1:3000',projectId='11111111-1111-4111-8111-111111111111';
  const first='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',second='bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb';
  const schema={target_label:'car',fields:[{key:'plate',name:'车牌',instruction:'看清再填写',options:[]},{key:'color',name:'颜色',instruction:'车身颜色',options:['白','黑']}]};
  const project={id:projectId,project_name:'车辆属性标注',task_type:'caption',status:'active',label_schema:{object_attributes:schema,labels:['car']}};
  const original={description:'保留的描述',labels:['car'],attribute_schema:schema,attributes:{plate:{value:'旧车牌',reason:'原标注'},color:{value:'白',reason:'原标注'}}};
  original.attributes.color.probabilities={'白':.7,'黑':.3};
  const makeAnn=id=>({id:'ann-'+id,asset_id:id,version:1,is_latest:true,review_status:'pending',annotation_source:'manual',annotation_data:structuredClone(original)});
  const annotations={[first]:makeAnn(first),[second]:makeAnn(second)};
  const models=[{id:'33333333-3333-4333-8333-333333333333',name:'Qwen在线',revision:4,unavailable_reason:null},
    {id:'44444444-4444-4444-8444-444444444444',name:'离线模型',revision:1,unavailable_reason:'模型离线'}];
  const writes=[],errors=[];let fail=false,defer=false,descriptionMode=false,release,started;
  const analysisStarted=new Promise(resolve=>started=resolve);
  page.on('pageerror',e=>errors.push(e.message));await page.addInitScript(()=>localStorage.setItem('token','isolated-single-image-analysis'));
  await page.route(base+'/api/**',async route=>{
    const req=route.request(),u=new URL(req.url()),p=u.pathname;
    if(req.method()!=='GET')writes.push({path:p,body:req.postDataJSON()});
    if(p==='/api/auth/me')return route.fulfill({json:{id:2,tenant_id:1,username:'标注员',role:'user'}});
    if(p==='/api/analytics/activity')return route.fulfill({json:{}});
    if(p==='/api/projects/'+projectId)return route.fulfill({json:project});
    if(p==='/api/assets/collections')return route.fulfill({json:[]});
    if(p.startsWith('/api/assets/')){
      const id=p.split('/')[3];
      if(p.endsWith('/content'))return route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="640" height="480"><rect width="640" height="480" fill="#314c74"/></svg>'});
      if(p.endsWith('/neighbors'))return route.fulfill({json:{position:id===first?1:2,total:2,previous_id:id===second?first:null,next_id:id===first?second:null}});
      return route.fulfill({json:{id,project_id:projectId,original_name:id===first?'当前车辆.png':'下一张车辆.png',width:640,height:480,source_type:'model_crop',annotation_status:'pending'}});
    }
    if(p.endsWith('/analysis-models'))return route.fulfill({json:models});
    if(p.endsWith('/analyze')){
      const id=p.split('/')[4];
      if(defer){started();await new Promise(resolve=>release=resolve);}
      return route.fulfill(fail?{status:504,json:{detail:'模型服务超时，请稍后重试'}}:{json:{asset_id:id,model_id:models[0].id,model_revision:4,...(descriptionMode?{description:'模型生成的描述'}:{attribute_schema:schema,
        attributes:{plate:{value:'苏L12345',reason:'清晰可见'},color:{value:'黑',reason:'车身可见'}}})}}).catch(()=>{});
    }
    if(p==='/api/annotations')return route.fulfill({json:{items:[annotations[u.searchParams.get('asset_id')]],total:1}});
    if(p.startsWith('/api/annotations/ann-')){
      const ann=Object.values(annotations).find(a=>p.endsWith(a.id));if(!ann||req.method()!=='PUT')throw Error('Unexpected annotation write '+p);
      ann.version++;ann.id+='-v2';ann.annotation_data=req.postDataJSON().annotation_data;return route.fulfill({json:ann});
    }
    if(req.method()!=='GET')throw Error('Unexpected write '+p);
    return route.fulfill({json:[]});
  });
  const field=()=>page.locator('.image-annotation-fields').getByLabel('车牌属性值',{exact:true});
  const open=async()=>{await page.getByRole('button',{name:'大模型分析当前图片',exact:true}).click();await page.getByRole('dialog').getByRole('combobox',{name:'单图分析模型',exact:true}).waitFor();};
  const start=()=>page.getByRole('dialog').getByRole('button',{name:/开始分析/}).click();
  const preview=()=>page.getByRole('dialog').getByText('分析结果（尚未保存）',{exact:true}).waitFor();
  await page.setViewportSize({width:1440,height:1000});await page.goto(base+'/annotate/'+first);
  await field().waitFor();await field().fill('人工修改');
  await open();
  await page.getByRole('dialog').getByRole('combobox',{name:'单图分析模型',exact:true}).click();
  const offline=page.locator('.ant-select-dropdown:visible .ant-select-item-option').filter({hasText:'离线模型'});
  if(await offline.getAttribute('aria-disabled')!=='true')throw Error('Offline model is selectable');
  await page.keyboard.press('Escape');await start();await preview();
  if(await field().inputValue()!=='人工修改')throw Error('Preview overwrote draft');
  await page.keyboard.press('r');
  if(writes.some(w=>!w.path.endsWith('/analyze')))throw Error('Preview saved or reviewed annotation');
  await page.screenshot({path:'output/playwright/current-image-analysis-preview.png',fullPage:true,animations:'disabled'});
  await page.getByRole('dialog').getByRole('button',{name:'关闭',exact:true}).click();
  if(await field().inputValue()!=='人工修改')throw Error('Closing preview changed draft');
  fail=true;await open();await start();await page.getByRole('dialog').getByText('模型服务超时，请稍后重试',{exact:true}).waitFor();
  if(await field().inputValue()!=='人工修改')throw Error('Failure changed draft');
  fail=false;await start();await preview();await page.getByRole('dialog').getByRole('button',{name:'填入标注区',exact:true}).click();
  if(await field().inputValue()!=='苏L12345')throw Error('Apply did not fill current attribute');
  if(writes.some(w=>!w.path.endsWith('/analyze')))throw Error('Apply automatically saved annotation');
  await page.getByRole('button',{name:'保存标注',exact:true}).click();
  await page.getByRole('button',{name:/保存标注/}).isDisabled();
  const saved=writes.find(w=>w.path.includes('/ann-'))?.body.annotation_data;
  if(!saved||saved.description!=='保留的描述'||saved.labels.join()!=='car'||saved.attributes.plate.value!=='苏L12345'||JSON.stringify(saved.attributes.color.probabilities)!==JSON.stringify(original.attributes.color.probabilities))throw Error('Apply lost other fields or read-only evidence '+JSON.stringify(saved));
  defer=true;await open();await start();await analysisStarted;await page.getByRole('dialog').getByRole('button',{name:'关闭',exact:true}).click();
  await page.getByRole('link',{name:'下一张',exact:true}).click();await page.getByText('下一张车辆.png',{exact:false}).waitFor();
  release?.();
  if(await field().inputValue()!=='旧车牌')throw Error('Late result changed next image');
  if(writes.filter(w=>w.path.includes('/ann-')).length!==1)throw Error('Unexpected annotation writes');
  if(writes.filter(w=>w.path.endsWith('/analyze')).some(w=>w.path!==`/api/annotations/assets/${first}/analyze`||w.body.model_id!==models[0].id||w.body.model_revision!==4||w.body.mode!=='attributes'))throw Error('Wrong image, revision or analysis mode sent');
  await page.screenshot({path:'output/playwright/current-image-analysis-button.png',fullPage:true,animations:'disabled'});
  descriptionMode=true;defer=false;project.task_type='vector';
  annotations[second].annotation_data={...structuredClone(original),description:'旧描述'};
  await page.goto(base+'/annotate/'+second);await open();
  await page.getByRole('dialog').getByRole('combobox',{name:'分析内容',exact:true}).click();
  await page.locator('.ant-select-dropdown:visible .ant-select-item-option-content').getByText('描述当前图片',{exact:true}).click();
  await start();await preview();
  await page.getByRole('dialog').getByText('模型生成的描述',{exact:true}).waitFor();
  await page.getByRole('dialog').getByRole('button',{name:'填入标注区',exact:true}).click();
  if(await page.locator('#image-annotation-description').inputValue()!=='模型生成的描述')throw Error('Description was not applied');
  await page.getByRole('button',{name:/保存标注/}).click();
  const descriptionSave=writes.filter(w=>w.path.includes('/ann-')).at(-1)?.body.annotation_data;
  if(descriptionSave?.description!=='模型生成的描述'||descriptionSave.labels.join()!=='car'||descriptionSave.attributes.plate.value!=='旧车牌')throw Error('Vector description apply lost other annotation fields');
  project.task_type='detection';project.label_schema={labels:['car']};
  const box={x:1,y:2,width:10,height:20,label:'car'};
  annotations[second].annotation_data={boxes:[box],description:'检测旧描述',extra_metadata:'保留'};
  await page.goto(base+'/annotate/'+second);await open();
  if(await page.getByRole('dialog').getByRole('combobox',{name:'分析内容',exact:true}).count())throw Error('Detection workspace should default to description');
  await start();await preview();await page.getByRole('dialog').getByRole('button',{name:'填入标注区',exact:true}).click();
  const description=page.getByRole('textbox',{name:'检测图片描述',exact:true});
  if(await description.inputValue()!=='模型生成的描述')throw Error('Detection description not filled');
  await page.getByRole('button',{name:/保存标注/}).click();
  await page.waitForFunction(()=>!document.querySelector('.detection-workspace .ant-btn-loading'));
  let detectionSave=writes.filter(w=>w.path.includes('/ann-')).at(-1)?.body.annotation_data;
  if(JSON.stringify(detectionSave.boxes)!==JSON.stringify([box])||detectionSave.description!=='模型生成的描述'||detectionSave.extra_metadata!=='保留')throw Error('Detection analysis changed boxes or metadata');
  await page.getByRole('button',{name:'1 · car',exact:true}).click();
  await page.getByRole('spinbutton',{name:'框x',exact:true}).fill('3');await page.getByRole('spinbutton',{name:'框x',exact:true}).blur();
  await page.getByRole('button',{name:/保存标注/}).click();
  await page.waitForFunction(()=>!document.querySelector('.detection-workspace .ant-btn-loading'));
  detectionSave=writes.filter(w=>w.path.includes('/ann-')).at(-1)?.body.annotation_data;
  if(detectionSave.boxes[0].x!==3||detectionSave.description!=='模型生成的描述')throw Error('Editing boxes lost description');
  await page.screenshot({path:'output/playwright/current-image-analysis-detection.png',fullPage:true,animations:'disabled'});
  if(errors.length)throw Error(JSON.stringify(errors));
  return {checks:['assigned annotator entry visible','offline choice disabled','current image and revision sent','preview and close preserve manual draft','shortcuts suppressed in modal','timeout preserves draft','apply requires explicit save and preserves other fields','cancel and late response cannot affect next image','vector description preserves classification and attributes','detection description preserves boxes and metadata','editing boxes keeps description'],analysis_requests:writes.filter(w=>w.path.endsWith('/analyze')),annotation_writes:writes.filter(w=>w.path.includes('/ann-')).length,errors};
}
