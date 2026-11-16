async page=>{
  page.setDefaultTimeout(12000);
  const origin='http://127.0.0.1:3000',id='11111111-1111-4111-8111-111111111111',assetId='asset-fixture',annotationId='annotation-fixture';
  const requests=[],unexpected=[],checks=[],errors=[];let projectReads=0;
  const project=()=>({id,project_name:'隔离检测项目',task_type:'detection',status:'active',label_schema:{labels:['car','bus']},total_assets:1,annotated_count:1,reviewed_count:0});
  const asset={id:assetId,project_id:id,original_name:'fixture.jpg',image_id:null,blob_path:'fixture.jpg',content_hash:'a'.repeat(64),file_size:10,format:'jpg',width:640,height:480,annotation_status:'completed',source_type:'upload',created_at:'2026-11-06T00:00:00Z',updated_at:'2026-11-06T00:00:00Z'};
  const annotation={id:annotationId,asset_id:assetId,annotation_data:{boxes:[{x:40,y:40,width:180,height:160,label:'car'},{x:280,y:70,width:100,height:140,label:'bus'}]},annotation_source:'auto',version:2,is_latest:true,review_status:'pending',created_at:'2026-11-06T00:00:00Z',updated_at:'2026-11-06T00:00:00Z'};
  const reviewItem={id:'review-fixture',job_id:'job-fixture',asset_id:assetId,annotation_id:annotationId,annotation_version:2,status:'needs_review',reason:'low_confidence',evidence:{},result:{boxes:annotation.annotation_data.boxes},created_at:'2026-11-06T00:00:00Z',project_id:id,project_name:project().project_name,asset_name:asset.original_name,job_kind:'preannotate'};
  const workJob={id:'work-fixture',operation:'crop',model_id:'detector',revision:1,total:10,counts:{pending:0,running:0,completed:8,failed:1,cancelled:1},created_at:'2026-11-06T00:00:00Z',model_name:'隔离模型'};
  const pageData=items=>({items,total:items.length,page:1,page_size:20});
  const addCheck=(condition,name)=>{if(!condition)throw Error(name);checks.push(name);};
  const user={id:1,username:'fixture-admin',role:'admin',tenant_id:1,is_active:true};
  const api=/^https?:\/\/[^/]+\/api\//;
  const handler=async route=>{
    const req=route.request(),url=new URL(req.url()),path=url.pathname,method=req.method();requests.push(`${method} ${path}${url.search}`);
    let data,status=200;
    if(path==='/api/auth/me')data=user;
    else if(path===`/api/projects/${id}`){projectReads++;if(projectReads===2){status=503;data={detail:'类别读取暂时失败'};}else data=project();}
    else if(path===`/api/projects/${id}/stats`)data={total_assets:1,annotated_count:1,reviewed_count:0};
    else if(path==='/api/projects')data=pageData([project()]);
    else if(path==='/api/annotations'&&method==='GET')data=pageData([annotation]);
    else if(path===`/api/annotations/${annotationId}`)data=annotation;
    else if(path===`/api/assets/${assetId}`)data=asset;
    else if(path===`/api/assets/${assetId}/content`)return route.fulfill({status:200,contentType:'image/png',body:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/d5sAAAAASUVORK5CYII=','base64')});
    else if(path==='/api/automation/review-queue')data=pageData([reviewItem]);
    else if(path==='/api/model-services')data=[{id:'detector',name:'隔离模型',revision:1,kind:'detection',protocol:'model_service',endpoint:'/detect',enabled:true,timeout_seconds:30,confidence:.25,iou:.45,labels:['car','bus'],label_mapping:{}}];
    else if(path==='/api/model-services/prompt-defaults')data={caption:'',review:'',match_review:'',training_analysis:'',scene:''};
    else if(path==='/api/assets')data=pageData([asset]);
    else if(path===`/api/projects/${id}/work/jobs`)data=[workJob];
    else if(path==='/api/projects/selection')data=pageData([project()]);
    else if(path.startsWith('/api/')){unexpected.push(`${method} ${path}${url.search}`);return route.fulfill({status:599,json:{detail:'未配置的隔离接口'}});}
    return route.fulfill({status,json:data});
  };
  page.on('pageerror',error=>errors.push(error.message));
  await page.addInitScript(user=>{localStorage.setItem('token','isolated-automation-review');localStorage.setItem('user',JSON.stringify(user));},user);
  await page.route(api,handler);
  try{
    await page.setViewportSize({width:1280,height:900});
    await page.goto(`${origin}/projects/${id}/review`);
    await page.getByRole('button',{name:'预览审核',exact:true}).waitFor();
    await page.getByRole('button',{name:'预览审核',exact:true}).click();
    const preview=page.getByRole('dialog',{name:'管理员审核预览'});await preview.waitFor();
    await preview.getByText('项目类别读取失败，框颜色暂不可用。',{exact:true}).waitFor();
    addCheck(await preview.getByRole('button',{name:'重试读取项目类别',exact:true}).isVisible(),'类别读取失败时显示明确提示与重试入口');
    addCheck(await preview.getByRole('button',{name:'批准',exact:true}).isDisabled(),'类别配置读取失败时禁止批准候选');
    addCheck(await preview.locator('svg rect').count()===6,'类别失败时仍显示中性预览框，不丢失框位置');
    addCheck(await preview.locator('svg rect').nth(2).getAttribute('stroke')==='#aeb7c6','类别失败时使用中性色，不静默套用哈希类别色');
    await preview.getByRole('button',{name:'重试读取项目类别',exact:true}).click();
    await preview.getByRole('alert').filter({hasText:'项目类别读取失败，框颜色暂不可用。'}).waitFor({state:'hidden'});
    addCheck(await preview.locator('svg rect[stroke="hsl(0 90% 60%)"]').count()===1,'重试成功后按项目类别顺序恢复颜色');
    addCheck(await preview.locator('svg rect[stroke="hsl(137.508 90% 60%)"]').count()===1,'多个类别使用稳定的项目类别颜色');
    const label=preview.locator('svg text').first();
    addCheck(await label.getAttribute('fill')==='white'&&await label.getAttribute('stroke')==='#0b101b'&&await label.getAttribute('paint-order')==='stroke'&&await label.getAttribute('stroke-linejoin')==='round','预览标签文字使用白色填充与黑色描边提高对比度');
    addCheck(await preview.getByRole('button',{name:'批准',exact:true}).isEnabled(),'类别读取成功后恢复批准功能');
    await preview.getByRole('button',{name:'关闭预览'}).click();

    await page.goto(`${origin}/annotations?project_id=${id}`);
    await page.getByText('数据处理与标注准备（按需）',{exact:true}).click();
    await page.getByRole('tab',{name:/后台处理队列/}).click();
    await page.getByText('已处理 10 / 10 张图片',{exact:true}).waitFor();
    addCheck(await page.getByText(/成功 8 · 失败 1 · 已取消 1/).count()===1,'阶段进度分开列出成功、失败和取消数量');
    addCheck(await page.locator('.ant-progress-status-exception').count()>0,'存在失败项时保留错误进度颜色');
    addCheck(unexpected.length===0,`所有接口均由隔离mock接管：${unexpected.join(', ')}`);
    addCheck(errors.length===0,`浏览器无运行时错误：${errors.join(', ')}`);
    return {checks,requests:requests.length,projectReads,unexpected,scope:'all /api/** requests intercepted; no real API calls or writes'};
  }catch(error){await page.screenshot({path:'output/playwright/automation-review-hardening-failure.png',fullPage:true,animations:'disabled'});throw Error(error.message+'\nBODY: '+(await page.locator('body').innerText()).slice(-7000)+'\nREQUESTS: '+requests.join('\n')+'\nUNEXPECTED: '+unexpected.join(', '));}
  finally{await page.goto('about:blank');await page.unroute(api,handler);}
}
