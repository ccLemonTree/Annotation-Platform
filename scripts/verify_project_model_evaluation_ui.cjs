async page=>{
  page.setDefaultTimeout(15000);
  const origin='http://127.0.0.1:3000',id='11111111-1111-4111-8111-111111111111';
  const checks=[],requests=[],writes=[],unexpected=[],errors=[],layoutFailures=[],previewPosts=[];
  let taskType='detection',role='admin',previewId='preview-fixture';
  const project=()=>({id,project_name:`隔离${taskType}项目`,task_type:taskType,status:'active',label_schema:{labels:['car']},total_assets:2,annotated_count:2,reviewed_count:2,created_at:'2026-11-06T00:00:00Z'});
  const assets=[1,2,3].map(n=>({id:`asset-${n}`,project_id:id,image_id:null,original_name:`fixture-${n}.jpg`,blob_path:`fixture/${n}.jpg`,content_hash:String(n).repeat(64),file_size:1024,format:'jpg',width:640,height:480,annotation_status:'reviewed',source_type:'upload',created_at:'2026-11-06T00:00:00Z',updated_at:'2026-11-06T00:00:00Z'}));
  const pageData=items=>({items,total:items.length,page:1,page_size:20});
  const addCheck=(ok,label)=>{if(!ok)throw Error(label);checks.push(label);};
  const user={id:1,username:'fixture-admin',role:'admin',tenant_id:1,is_active:true};
  const model=(kind)=>({id:`${kind}-model`,name:`隔离${kind}模型`,revision:1,kind,protocol:kind==='detection'?'model_service':kind==='caption'?'openai_chat':'openai_embedding',model_name:'fixture',enabled:true,timeout_seconds:30,confidence:.25,iou:.45,labels:['car'],label_mapping:{},connection_check:{status:'online',checked_revision:1}});
  const previewRun=(kind=taskType)=>({id:previewId,name:'隔离预览',status:'completed',total:2,processed:2,failed_count:0,set_id:'',model:{id:`${kind}-model`,revision:1,name:`隔离${kind}模型`,score_type:'calibrated'},config:{inference_confidence:.001,confidence:.25,match_iou:.5,nms_iou:.45,label_mapping:{},labels:['car']},metrics:null,error:null,created_at:'2026-11-06T00:00:00Z',updated_at:'2026-11-06T00:00:00Z',items:[],page:1,page_size:20});
  const set={id:'set-fixture',name:'隔离验证集',project_id:id,labels:['car'],total:2,source:{kind:'approved'},created_at:'2026-11-06T00:00:00Z'};
  const api=/^https?:\/\/[^/]+\/api\//;
  const handler=async route=>{
    const req=route.request(),url=new URL(req.url()),path=url.pathname,method=req.method(),search=url.searchParams;
    let body;try{body=req.postDataJSON();}catch{}
    requests.push({path,method,search:url.search});if(method!=='GET')writes.push({path,method,body});
    let data,status=200,headers;
    if(path==='/api/auth/me')data=user;
    else if(path===`/api/projects/${id}`)data=project();
    else if(path==='/api/projects')data=pageData([project()]);
    else if(path.startsWith('/api/assets/')&&path.endsWith('/content'))return route.fulfill({status:200,contentType:'image/png',body:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/d5sAAAAASUVORK5CYII=','base64')});
    else if(path==='/api/assets'){
      if(search.get('project_id')!==id)unexpected.push(`asset list missing project_id: ${url.search}`);
      data=pageData(assets);headers={'x-asset-filename-filter':'1'};
    }else if(path==='/api/model-services')data=[model(search.get('kind')||'detection')];
    else if(path==='/api/model-services/prompt-defaults')data={caption:'Describe visible content',review:'',match_review:'',training_analysis:'',scene:''};
    else if(path==='/api/model-previews/config')data={max_images:100,max_upload_size:104857600,max_expanded_size:104857600,max_image_size:10485760,max_video_size:104857600};
    else if(path==='/api/model-previews'&&method==='POST'){
      const raw=req.postDataBuffer()?.toString('utf8')||'';
      const requestPart=raw.match(/name="request"\r\n\r\n([\s\S]*?)\r\n--/);
      let previewRequest;try{previewRequest=JSON.parse(requestPart?.[1]||'{}');}catch{}
      previewPosts.push({request:previewRequest,raw});previewId=`preview-${previewPosts.length}`;data=previewRun(previewRequest?.task_type||taskType);data.id=previewId;status=201;
    }else if(path==='/api/model-previews'&&method==='GET')data={...pageData([previewRun()]),items:[previewRun()]};
    else if(path.startsWith('/api/model-previews/')&&path.endsWith('/items/fixture-item/image'))return route.fulfill({status:200,contentType:'image/png',body:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/d5sAAAAASUVORK5CYII=','base64')});
    else if(path.startsWith('/api/model-previews/')&&path.endsWith('/image'))return route.fulfill({status:200,contentType:'image/png',body:Buffer.from('')});
    else if(path.startsWith('/api/model-previews/'))data=previewRun();
    else if(path==='/api/validation/sets'&&method==='GET')data={...pageData([set]),items:[set]};
    else if(path.startsWith('/api/export/projects/')&&path.endsWith('/snapshots'))data=[];
    else if(path==='/api/validation/sets'&&method==='POST'){data={...set,...body};status=201;}
    else if(path==='/api/validation/runs'&&method==='GET')data=pageData([]);
    else if(path==='/api/validation/runs'&&method==='POST'){data={...previewRun(),id:'validation-fixture',set_id:body.set_id,status:'pending'};status=201;}
    else if(path==='/api/training/runs')data=pageData([]);
    else if(path==='/api/training/servers')data=[{id:'default',name:'隔离服务器',enabled:true,revision:1,source:'ssh_manager'}];
    else if(path==='/api/training/catalog')data={swift_sft:{name:'Swift',image:'fixture/swift:isolated',defaults:{},environment:{},schema:{properties:{}}},wemm:{name:'WEMM',image:'fixture/wemm:isolated',defaults:{},environment:{},schema:{properties:{}}}};
    else if(path==='/api/training/defaults')data={model_path:'/fixture/model',output_root:'/fixture/runs'};
    else if(path==='/api/gpu-resources')data={server_name:'fixture',collected_at:'2026-11-06T00:00:00Z',cached:true,processes_complete:true,warnings:[],gpus:[],processes:[]};
    else if(path==='/api/training/runs'&&method==='GET')data=pageData([]);
    else if(path==='/api/validation/runs'&&method==='GET')data=pageData([]);
    else if(path.startsWith('/api/')){unexpected.push(`${method} ${path}${url.search}`);return route.fulfill({status:599,json:{detail:'未配置的隔离接口'}});}
    return route.fulfill({status,headers,json:data});
  };
  page.on('pageerror',error=>errors.push(error.message));
  await page.addInitScript(user=>{localStorage.setItem('token','isolated-project-model-eval');localStorage.setItem('user',JSON.stringify(user));},user);
  await page.route(api,handler);
  const checkProjectQueries=()=>{
    const scoped=[...new Map(requests.filter(r=>['/api/model-previews','/api/validation/sets','/api/validation/runs','/api/training/runs'].includes(r.path)&&r.method==='GET').map(r=>[`${r.path}${r.search}`,r])).values()];
    for(const row of scoped)addCheck(new URLSearchParams(row.search).get('project_id')===id,`${row.path} 按当前项目过滤`);
  };
  const layout=async(label)=>{
    const measured=await page.evaluate(()=>({html:[document.documentElement.scrollWidth,document.documentElement.clientWidth],overflow:[...document.querySelectorAll('body *')].filter(el=>{const r=el.getBoundingClientRect();return r.right>innerWidth+1&&r.width>0;}).slice(0,6).map(el=>({tag:el.tagName,cls:typeof el.className==='string'?el.className:'',right:Math.round(el.getBoundingClientRect().right)}))}));
    if(measured.html[0]>measured.html[1]+1)layoutFailures.push(`${label} ${JSON.stringify(measured)}`);else checks.push(`${label}无横向溢出`);
  };
  try{
    await page.setViewportSize({width:1440,height:1000});
    for(const type of ['detection','caption','vector']){
      taskType=type;previewId=`preview-${type}`;
      await page.goto(`${origin}/projects/${id}/test`);
      await page.getByRole('navigation',{name:'项目导航'}).waitFor();
      const nav=page.getByRole('navigation',{name:'项目导航'});
      await nav.getByRole('link',{name:'测试与评估',exact:true}).waitFor();
      await page.getByText(project().project_name,{exact:true}).first().waitFor();
      addCheck(await page.getByText(project().project_name,{exact:true}).count()>=1,`${type}页面显示当前项目名称`);
      await page.getByRole('tab',{name:'快速测试',exact:true}).waitFor();
      await page.getByRole('tab',{name:'数据集评估',exact:true}).waitFor();
      addCheck(await page.getByRole('tab').count()===2,`${type}项目内只显示快速测试和数据集评估两种模式`);
      addCheck(await page.getByRole('tab').filter({hasText:/目标检测|图片描述|向量检索/}).count()===0,`${type}嵌入页不显示额外任务类型标签`);
      const mode=page.getByRole('tab',{name:'数据集评估'});await mode.click();
      addCheck(new URL(page.url()).pathname===`/projects/${id}/test`&&await page.getByText(project().project_name,{exact:true}).count()>=1,`${type}切换模式仍留在当前项目`);
      await page.getByRole('tab',{name:'快速测试'}).click();
      const source=page.getByRole('combobox',{name:'测试图片来源'});await source.waitFor();
      await source.click();
      addCheck(await page.locator('.ant-select-dropdown:visible').getByText('上传图片或文件',{exact:true}).count()===1,`${type}快速测试保留上传文件来源`);
      await page.locator('.ant-select-dropdown:visible').getByText('项目已有图片',{exact:true}).click();
      await page.getByRole('button',{name:'选择项目图片',exact:true}).click();
      const picker=page.getByRole('dialog',{name:'选择项目测试图片'});await picker.waitFor();
      await picker.getByRole('row',{name:/fixture-1\.jpg/}).getByRole('checkbox').check();
      await picker.getByRole('row',{name:/fixture-2\.jpg/}).getByRole('checkbox').check();
      await picker.getByRole('button',{name:'完成选图',exact:true}).click();
      const orderedImages=page.locator('ol li');
      await orderedImages.nth(0).waitFor();await orderedImages.nth(1).waitFor();
      addCheck((await orderedImages.nth(0).innerText()).includes('fixture-1.jpg')&&(await orderedImages.nth(1).innerText()).includes('fixture-2.jpg'),`${type}项目图片按选择顺序编号`);
      const expectedModelType=type==='vector'?'embedding':type;
      const modelPicker=type==='detection'?page.getByRole('combobox',{name:'预览检测模型'}):page.getByRole('combobox',{name:'语义模型服务'});
      addCheck(await modelPicker.count()===1,`${type}任务类型固定到对应模型选择器`);
      await modelPicker.click();await page.locator('.ant-select-dropdown:visible').getByText(`隔离${expectedModelType}模型`,{exact:true}).click();
      if(type==='vector'){
        await page.getByRole('textbox',{name:'向量查询文本'}).fill('红色汽车');
      }
      await page.screenshot({path:`output/playwright/project-model-evaluation-${type}-1440.png`,fullPage:true,animations:'disabled'});
      const before=previewPosts.length;
      const previewResponse=page.waitForResponse(response=>new URL(response.url()).pathname==='/api/model-previews'&&response.request().method()==='POST');
      await page.getByRole('button',{name:type==='detection'?'开始模型预览':'开始效果预览',exact:true}).click();
      await previewResponse;
      const posted=previewPosts.slice(before).at(-1);
      addCheck(!!posted,`${type}项目图片可提交预览任务`);
      addCheck(posted.request.project_id===id&&JSON.stringify(posted.request.asset_ids)===JSON.stringify(['asset-1','asset-2']),`${type}预览请求带项目ID和按序asset_ids`);
      addCheck(!/name="files"/.test(posted.raw),`${type}项目图片预览不重复上传文件`);
      if(type==='caption')addCheck(posted.request.prompt==='描述图片中可见的内容，不要猜测。','caption请求中的默认提示词保留原始中文');
      if(type==='vector')addCheck(posted.request.query_text==='红色汽车','vector请求中的查询文本保留原始中文');
      if(type==='detection'){
        const source=page.getByRole('combobox',{name:'测试图片来源'});await source.click();
        await page.locator('.ant-select-dropdown:visible').getByText('上传图片或文件',{exact:true}).click();
        await page.locator('input[type="file"]').first().setInputFiles({name:'fixture-upload.jpg',mimeType:'image/jpeg',buffer:Buffer.from([0xff,0xd8,0xff,0xd9])});
        const uploadResponse=page.waitForResponse(response=>new URL(response.url()).pathname==='/api/model-previews'&&response.request().method()==='POST');
        await page.getByRole('button',{name:'开始模型预览',exact:true}).click();await uploadResponse;
        const uploaded=previewPosts.at(-1);
        addCheck(uploaded.request.project_id===id&&!uploaded.request.asset_ids&&/name="files"/.test(uploaded.raw),'检测项目仍可上传图片测试，提交包含文件且不带项目asset_ids');
      }
      await page.waitForFunction(()=>document.querySelector('.ant-empty')||document.body.innerText.includes('隔离预览'),null,{timeout:3000}).catch(()=>{});
      await page.getByRole('tab',{name:'数据集评估'}).click();
      if(type==='detection'){
        await page.getByRole('button',{name:'创建验证集',exact:true}).click();
        const dialog=page.getByRole('dialog',{name:'创建验证集',exact:true});await dialog.waitFor();
        const projectSelect=dialog.getByRole('combobox',{name:'验证项目'});
        addCheck(await projectSelect.isDisabled(),'检测验证集创建锁定当前项目');
        await page.keyboard.press('Escape');
        await page.getByRole('button',{name:'评估模型',exact:true}).click();
        const evalDialog=page.getByRole('dialog',{name:'评估检测模型',exact:true});await evalDialog.waitFor();
        const beforeRuns=requests.filter(row=>row.path==='/api/training/runs'&&row.method==='GET').length;
        const sourcePicker=evalDialog.getByRole('combobox',{name:'评估模型来源'});await sourcePicker.click();
        const runResponse=page.waitForResponse(response=>new URL(response.url()).pathname==='/api/training/runs'&&response.request().method()==='GET');
        await page.locator('.ant-select-dropdown:visible').getByText('训练权重',{exact:true}).click();
        await runResponse;
        const runRequest=requests.filter(row=>row.path==='/api/training/runs'&&row.method==='GET').at(-1);
        addCheck(!!runRequest&&new URLSearchParams(runRequest.search).get('project_id')===id&&requests.filter(row=>row.path==='/api/training/runs'&&row.method==='GET').length>beforeRuns,'检测评估的训练权重列表固定当前项目');
        await evalDialog.locator('.ant-modal-close').click();await evalDialog.waitFor({state:'hidden'});
      }else{
        const create=page.getByRole('button',{name:'创建验证集',exact:true});await create.click();
        const dialog=page.getByRole('dialog',{name:/创建.*验证集/});await dialog.waitFor();
        const projectSelect=dialog.getByRole('combobox',{name:'语义验证项目'});
        addCheck(await projectSelect.isDisabled(),`${type}验证集创建锁定当前项目`);
        await page.keyboard.press('Escape');
        await page.getByRole('button',{name:'验证模型',exact:true}).click();
        const evalDialog=page.getByRole('dialog',{name:/验证模型/});await evalDialog.waitFor();
        const sourcePicker=evalDialog.getByRole('combobox',{name:'语义模型来源'});await sourcePicker.click();
        const runResponse=page.waitForResponse(response=>new URL(response.url()).pathname==='/api/training/runs'&&response.request().method()==='GET');
        await page.locator('.ant-select-dropdown:visible').getByText('服务器训练权重 / 合并模型',{exact:true}).click();
        await runResponse;
        const runRequest=requests.filter(row=>row.path==='/api/training/runs'&&row.method==='GET').at(-1);
        addCheck(new URLSearchParams(runRequest.search).get('project_id')===id,`${type}评估的训练权重列表固定当前项目`);
        await evalDialog.locator('.ant-modal-close').click();await evalDialog.waitFor({state:'hidden'});
      }
      await page.getByText(project().project_name,{exact:true}).first().click();
      await page.evaluate(()=>{window.scrollTo(0,0);document.querySelectorAll('.ant-table-body').forEach(element=>element.scrollLeft=0);});await page.waitForTimeout(300);
      await page.screenshot({path:`output/playwright/project-model-evaluation-${type}-assessment-1440.png`,fullPage:true,animations:'disabled'});
      await page.setViewportSize({width:390,height:844});await page.waitForTimeout(200);await layout(`${type} 390px测试评估页`);
      await page.screenshot({path:`output/playwright/project-model-evaluation-${type}-390.png`,fullPage:true,animations:'disabled'});
      await page.setViewportSize({width:1440,height:1000});
    }
    for(const unsupported of ['classification','segmentation']){
      taskType=unsupported;await page.goto(`${origin}/projects/${id}/test`);
      await page.getByText(/当前.*不支持|暂不支持|无可用测试/).waitFor();
      addCheck(true,`${unsupported}项目显示清晰的不支持说明`);
    }
    const uploadPicker=page.getByRole('button',{name:/上传图片或文件/});
    if(await uploadPicker.count())addCheck(true,'仍保留上传文件快速测试入口');
    checkProjectQueries();
    addCheck(unexpected.length===0,`所有API均被隔离mock接管：${unexpected.join(', ')}`);
    addCheck(errors.length===0,`浏览器无运行时错误：${errors.join(', ')}`);
    addCheck(layoutFailures.length===0,`390px页面无横向溢出：${layoutFailures.join('; ')}`);
    return {checks,requests:requests.length,writes,previewPosts:previewPosts.map(p=>({request:p.request,hasFiles:/name="files"/.test(p.raw)})),scope:'all HTTP(S) /api/** intercepted; no real API calls or business writes'};
  }catch(error){await page.screenshot({path:'output/playwright/project-model-evaluation-failure.png',fullPage:true,animations:'disabled'});throw Error(error.message+'\nBODY: '+(await page.locator('body').innerText()).slice(-7000)+'\nREQUESTS: '+JSON.stringify(requests)+'\nUNEXPECTED: '+unexpected.join(', '));}
  finally{await page.goto('about:blank');await page.unroute(api,handler);}
}
