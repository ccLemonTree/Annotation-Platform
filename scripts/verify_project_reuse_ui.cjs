async page=>{
  const target={id:'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',project_name:'目标检测项目',task_type:'detection',status:'active',created_at:'2026-11-02T00:00:00Z',label_schema:{labels:['人']},total_assets:0,annotated_count:0,reviewed_count:0};
  const source={...target,id:'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',project_name:'来源检测项目'};
  const image={id:'cccccccc-cccc-4ccc-8ccc-cccccccccccc',original_name:'reuse.jpg',scene_labels:['工厂'],scene_status:'completed',collection_names:['历史集'],has_imported_annotation:true,file_size:1024};
  const pattern=/^http:\/\/localhost:3000\/api\//;const reads=[],writes=[];
  const uploadImage='dddddddd-dddd-4ddd-8ddd-dddddddddddd';let uploadAttempts=0,attached=false;
  const upload={id:'upload-fixture',filename:'batch.png',collection_id:'collection-fixture',status:'completed',size:10,uploaded_parts:[],uploaded_bytes:10,chunk_size:1024,min_long_edge:0,result:{imported:0,duplicates:1,filtered:0,invalid:0,ignored:0,errors:[]}};
  await page.addInitScript(()=>{if(location.protocol==='http:')localStorage.setItem('token','isolated-reuse-token');});
  await page.route(pattern,async route=>{
    const request=route.request(),url=new URL(request.url()),path=url.pathname;reads.push(url.href);
    if(request.method()!=='GET'){
      const body=request.postDataJSON();writes.push({path,body});
      if(path==='/api/image-uploads')return route.fulfill({json:upload});
      if(path==='/api/images/associate'&&body.image_ids.includes(uploadImage)){
        uploadAttempts++;if(uploadAttempts===1)return route.fulfill({status:503,json:{detail:'隔离测试：关联中断'}});
        attached=true;
        return route.fulfill({json:{added_count:1,asset_ids:[],conflicts:0}});
      }
      return route.fulfill({json:{added_count:0,skipped_count:1,asset_ids:[],annotated:0,reused_annotations:0,conflicts:1,without_annotations:0}});
    }
    let data;
    if(path==='/api/auth/me')data={id:1,role:'admin',username:'fixture',tenant_id:1};
    else if(path==='/api/projects')data={items:[target,source],total:2};
    else if(path===`/api/projects/${target.id}`)data=target;
    else if(path==='/api/images/collections')data=[{id:'collection-fixture',name:'历史集',image_count:1,ingest_config:{scenes:['工厂','河边']}}];
    else if(path==='/api/images')data={items:[image],total:1};
    else if(path==='/api/images/selection')data={image_ids:[image.id],total:1};
    else if(path==='/api/image-uploads/config')data={chunk_size:1024,max_file_size:100000,max_image_size:100000,max_expanded_size:100000,max_entries:100,retention_days:1};
    else if(path==='/api/image-uploads/upload-fixture/images')data={image_ids:[uploadImage],total:1,page:1,page_size:1000};
    else if(path==='/api/image-uploads')data=[];
    else if(path==='/api/images/ingest-settings')data={min_long_edge:0,classification_enabled:false};
    else if(path==='/api/assets')data={items:attached?[{...image,id:'uploaded-asset',project_id:target.id,annotation_status:'pending',scene_status:'unclassified',scene_metadata:{basis:'device_name',device_scene_status:'pending'},device_info:{sources:[{device_name:'工厂南门摄像头'},{device_name:'仓库通道摄像头'}]}}]:[],total:attached?1:0};
    else if(path.endsWith('/content'))return route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/>'});
    else data=[];
    await route.fulfill({headers:{'X-Annotation-Label-Filter':'1','X-Asset-Scene-Filter':'1'},json:data});
  });
  const choose=async(name,text)=>{const field=page.getByRole('combobox',{name,exact:true});await field.click();await page.locator('.ant-select-dropdown:visible .ant-select-item-option-content').getByText(text,{exact:true}).click();};
  try{
    await page.goto(`http://localhost:3000/projects/${target.id}/images`);
    await page.getByRole('button',{name:'复用已有数据',exact:true}).click();
    const drawer=page.getByRole('dialog',{name:`复用已有数据到 ${target.project_name}`});await drawer.waitFor();
    await choose('复用来源项目',source.project_name);
    await choose('来源标注标签','人');await choose('选择器场景','工厂');
    await drawer.getByRole('checkbox',{name:'复制来源项目已审核标注'}).check();
    if(await drawer.getByRole('checkbox',{name:'带入已有YOLO标注'}).isChecked())throw Error('复制模式未互斥');
    await drawer.getByRole('button',{name:'选择当前筛选全部图片'}).click();
    await drawer.getByRole('button',{name:`添加到 ${target.project_name}`}).click();
    const confirm=page.getByRole('dialog',{name:'确认复用已审核标注'});await confirm.getByRole('button',{name:'确认复用'}).click();
    await drawer.waitFor({state:'hidden'});
    const association=writes.find(write=>write.path==='/api/images/associate');
    if(association?.body.project_id!==target.id||association.body.source_project_id!==source.id||association.body.include_source_annotations!==true||association.body.include_imported_annotations)throw Error('复用目的地或复制模式错误');
    const selection=reads.find(value=>new URL(value).pathname==='/api/images/selection');const query=new URL(selection).searchParams;
    if(query.get('source_project_id')!==source.id||query.get('label')!=='人'||query.get('scene')!=='工厂'||query.get('review_status')!=='approved'||query.has('exclude_project_id'))throw Error('组合筛选/补标注选择范围不一致');
    await page.getByText(/已有标注保留 1/).waitFor();
    await choose('项目标注标签','人');await page.getByRole('combobox',{name:'项目图片场景'}).fill('工厂');await page.getByRole('combobox',{name:'项目图片场景'}).press('Enter');
    if(!reads.some(value=>{const url=new URL(value);return url.pathname==='/api/assets'&&url.searchParams.get('label')==='人'&&url.searchParams.get('scene')==='工厂';}))throw Error('项目标签与场景未共同筛选');
    const link=page.getByRole('link',{name:'项目数据拉取'});if(!(await link.getAttribute('href')).includes(target.id))throw Error('拉取入口丢项目');
    await page.getByRole('button',{name:'上传到项目',exact:true}).click();
    const modal=page.getByRole('dialog',{name:`上传到项目 ${target.project_name}`});
    await modal.getByLabel('上传图片集名称',{exact:true}).fill('历史集');
    await modal.getByLabel('上传图片或压缩包',{exact:true}).setInputFiles({name:'batch.png',mimeType:'image/png',buffer:Buffer.from('fixture')});
    await modal.getByRole('button',{name:'开始 / 继续上传',exact:true}).click();
    await modal.getByRole('alert').filter({hasText:'503'}).waitFor();
    await modal.getByRole('button',{name:'开始 / 继续上传',exact:true}).click();
    await modal.getByText(/已关联“目标检测项目”新增 1 张/).waitFor();
    const uploadWrites=writes.filter(write=>write.path==='/api/images/associate'&&write.body.image_ids.includes(uploadImage));
    if(uploadWrites.length!==2||uploadWrites.some(write=>write.body.project_id!==target.id||write.body.image_ids.length!==1||write.body.image_ids.includes(image.id)))throw Error('上传未精确按批次关联或失败后未重试');
    await modal.getByRole('button',{name:'关闭',exact:true}).click();
    await page.getByText('待设备分类',{exact:true}).waitFor();
    await page.getByText('按设备名',{exact:true}).waitFor();
    await page.getByRole('cell',{name:'工厂南门摄像头、仓库通道摄像头',exact:true}).waitFor();
    await page.getByText('隔离测试：关联中断',{exact:true}).waitFor({state:'hidden'});
    await page.locator('.ant-spin-spinning').waitFor({state:'hidden'});
    await page.screenshot({path:'output/playwright/project-reuse-desktop.png',fullPage:true,animations:'disabled'});
    await page.setViewportSize({width:390,height:844});
    await page.waitForFunction(()=>getComputedStyle(document.querySelector('.platform-main')).marginLeft==='0px');
    if(await page.locator('html').evaluate(element=>element.scrollWidth>element.clientWidth+1))throw Error('项目图片手机端溢出');
    await page.getByRole('button',{name:'复用已有数据',exact:true}).click();await drawer.waitFor();
    if(await page.locator('html').evaluate(element=>element.scrollWidth>element.clientWidth+1))throw Error('数据复用抽屉手机端溢出');
    await page.screenshot({path:'output/playwright/project-reuse-mobile.png',fullPage:true,animations:'disabled'});
    await drawer.getByRole('button',{name:'取消选图',exact:true}).click();
    await page.setViewportSize({width:1440,height:1000});
    await page.goto('http://localhost:3000/projects');
    if(await page.getByRole('menuitem',{name:'共享数据源',exact:true}).count())throw Error('共享数据源仍作为一级菜单');
    await page.getByRole('link',{name:'共享数据源',exact:true}).first().waitFor();
    await page.getByRole('button',{name:'进入项目',exact:true}).first().waitFor();
    await page.locator('.ant-spin-spinning').waitFor({state:'hidden'});
    await page.screenshot({path:'output/playwright/project-entry-desktop.png',fullPage:true,animations:'disabled'});
    return {checks:['项目内复用入口','来源标签+场景组合全选','仅approved标注显式复制确认','YOLO模式互斥','已有标注保护计数','项目标签场景筛选与拉取上下文','项目上传仅关联实际批次且失败可重试'],scope:'全部根/api隔离，无真实图片或标注改动'};
  }catch(error){await page.screenshot({path:'output/playwright/project-reuse-failure.png',fullPage:true});throw error;}
  finally{await page.goto('about:blank');await page.unroute(pattern);}
}
