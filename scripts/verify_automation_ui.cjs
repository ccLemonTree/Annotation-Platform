async (page) => {
  page.setDefaultTimeout(12000);
  const origin = 'http://127.0.0.1:3000';
  const checks = [], writes = [], unexpected = [], projectPages = [], annotationReads = [], queries = [];
  let role = 'admin', mode = 'normal', listCalls = 0, queueCalls = 0, release, held, conflict = false;
  const base = { key:'model', base_url:'http://fixture', endpoint:'/detect', model_name:'fixture-vl', enabled:true,
    timeout_seconds:60, confidence:.31, iou:.52, labels:['fire'], label_mapping:{smoke:'fire'}, revision:1 };
  const models = [
    {...base,id:'detector',name:'检测配置',kind:'detection',protocol:'model_service'},
    {...base,id:'disabled',name:'已停用检测',kind:'detection',protocol:'model_service',enabled:false},
    {...base,id:'wrong',name:'其他描述协议',kind:'caption',protocol:'model_service'},
    {...base,id:'reviewer',name:'视觉审核服务（改名）',kind:'caption',protocol:'openai_chat'},
  ];
  let jobs = [
    {id:'job-running',project_id:'p1',kind:'preannotate',status:'running',total:10,processed:2,approved_count:1,needs_review_count:1,failed_count:0,config:{},created_at:'2026-10-15T00:00:00Z'},
    {id:'job-failed',project_id:'p1',kind:'review',status:'failed',total:4,processed:3,approved_count:1,needs_review_count:1,failed_count:1,error:'fixture failure',config:{},created_at:'2026-10-15T00:00:00Z'},
  ];
  const item = {id:'item-1',job_id:'job-running',asset_id:'asset-1',annotation_id:'annotation-1',annotation_version:3,
    status:'needs_review',reason:'低分目标待确认',evidence:{boxes:[{index:0,verdict:'uncertain',reason:'fixture box uncertain'}],
      completeness:'fail',negative:'uncertain',reason:'fixture image incomplete',rounds:[{reason:'fixture negative needs admin',crops:[{index:0,bounds:[0,12,200,150]}]}]},
    result:{boxes:[{label:'fire',x:20,y:30,width:150,height:90,score:.6}]},created_at:'2026-10-15T00:00:00Z'};
  let queue = [item,
    {...item,id:'item-zero',asset_id:'asset-zero',annotation_id:'annotation-zero',reason:'零框必须人工核对',evidence:{boxes:[],completeness:'pass',negative:'pass',reason:'Qwen negative pass, admin final'},result:{boxes:[]}},
    {...item,id:'item-unknown',asset_id:'asset-unknown',annotation_id:null,annotation_version:0,reason:'未映射类别',result:{boxes:[{label:'unknown',x:20,y:30,width:40,height:60}]}},
    {...item,id:'item-stale',asset_id:'asset-stale',annotation_id:'annotation-stale',reason:'版本变更'},
    {...item,id:'item-deleted',asset_id:'asset-deleted',annotation_id:'annotation-deleted',reason:'原图或标注已删除'},
  ];
  const paginate = (items,url,total=items.length) => ({items,total,page:Number(url.searchParams.get('page')||1),page_size:Number(url.searchParams.get('page_size')||20)});
  const handler = async route => {
    const req=route.request(), url=new URL(req.url()), path=url.pathname, method=req.method();
    if(!path.startsWith('/api/'))return route.continue();
    if(method==='GET')queries.push(path+url.search);
    if (path==='/api/auth/me') return route.fulfill({json:{id:1,username:'fixture',role}});
    if (path==='/api/projects') {
      const p=Number(url.searchParams.get('page')||1); projectPages.push(p);
      const projects=p===1 ? Array.from({length:100},(_,i)=>({id:'p'+(i+1),project_name:'检测项目 '+(i+1),task_type:'detection',label_schema:{labels:['fire']}}))
        : [{id:'p101',project_name:'分页末尾项目 101',task_type:'detection',label_schema:{labels:['fire']}}];
      return route.fulfill({json:paginate(projects,url,101)});
    }
    if (path==='/api/model-services') return route.fulfill({json:models});
    if (path==='/api/automation/jobs' && method==='GET') {
      listCalls++;
      const snapshot=jobs.map(j=>({...j})), oldMode=mode;
      if(mode==='hold') { held=new Promise(resolve=>{release=resolve;}); await held; }
      try {
        if(oldMode==='error')return await route.fulfill({status:503,json:{detail:'fixture unavailable'}});
        return await route.fulfill({json:paginate(url.searchParams.get('project_id')==='p101' ? [{...snapshot[0],id:'job-other',project_id:'p101',status:'completed'}] : snapshot,url,url.searchParams.get('project_id')==='p101'?1:31)});
      } catch(error) { if(!/closed|handled|invalid|intercept|cancel/i.test(error.message))throw error; return; }
    }
    if(path==='/api/automation/jobs' && method==='POST') {
      writes.push({path,payload:req.postDataJSON()}); return route.fulfill({json:{...jobs[0],id:'created'}});
    }
    if(/\/api\/automation\/jobs\/[^/]+\/(cancel|retry)$/.test(path) && method==='POST') {
      writes.push({path}); const id=path.split('/')[4]; jobs=jobs.map(j=>j.id===id?{...j,status:path.endsWith('/cancel')?'canceled':'pending'}:j);
      return route.fulfill({json:jobs.find(j=>j.id===id)});
    }
    if(/\/api\/automation\/jobs\/[^/]+\/items$/.test(path)) {
      const status=url.searchParams.get('status');return route.fulfill({json:paginate(status&&status!=='needs_review'?[]:[item],url, status&&status!=='needs_review'?0:31)});
    }
    if(path==='/api/automation/review-queue') {
      queueCalls++;
      if(role!=='admin'){unexpected.push('ordinary user requested review queue');return route.fulfill({status:403,json:{detail:'forbidden'}});}
      return route.fulfill({json:paginate(queue,url,31)});
    }
    if(/\/api\/automation\/items\/[^/]+\/resolve$/.test(path) && method==='POST') {
      writes.push({path,payload:req.postDataJSON()});
      if(conflict&&req.postDataJSON().approved)return route.fulfill({status:409,json:{detail:'标注版本已变化，请刷新后重新核对'}});
      queue=queue.filter(i=>i.id!==path.split('/')[4]);return route.fulfill({json:{...item,status:'approved'}});
    }
    if(path.startsWith('/api/annotations/')) {
      if(path.endsWith('annotation-deleted'))return route.fulfill({status:404,json:{detail:'标注已删除'}});
      annotationReads.push(path);const id=path.split('/')[3], asset=id.replace('annotation','asset');
      return route.fulfill({json:{id,asset_id:asset,version:id==='annotation-stale'?4:3,is_latest:true,annotation_source:id==='annotation-zero'?'model':'manual',annotation_data:{boxes:id==='annotation-zero'?[]:item.result.boxes}}});
    }
    if(path==='/api/assets/asset-deleted'||path==='/api/assets/asset-deleted/content')return route.fulfill({status:404,json:{detail:'原图已删除'}});
    if(path.startsWith('/api/assets/')&&!path.endsWith('/content'))return route.fulfill({json:{id:path.split('/')[3],project_id:'p1',width:640,height:480,original_name:'fixture.png'}});
    if(path.endsWith('/content'))return route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="640" height="480"><rect width="640" height="480" fill="#e2e8f0"/></svg>'});
    unexpected.push(method+' '+path); return route.fulfill({status:500,json:{detail:'Unexpected mocked request'}});
  };
  const main = await (await page.request.get(origin+'/src/main.tsx')).text();
  const reactDomUrl = main.match(/["']([^"']*react-dom_client[^"']*)["']/)?.[1];
  if(!reactDomUrl)throw Error('Cannot locate Vite React runtime');
  const runtime = await (await page.request.get(origin+'/src/components/AssetImage.tsx')).text();
  const reactUrl=runtime.match(/["']([^"']*\/react\.js\?[^"']*)["']/)[1];
  const previewSource=await (await page.request.get(origin+'/src/pages/AnnotationsPage.tsx')).text();
  const routerUrl=previewSource.match(/["']([^"']*\/react-router-dom\.js\?[^"']*)["']/)[1];
  const appPattern='**/src/App.tsx*';
  await page.route(appPattern,route=>route.fulfill({contentType:'application/javascript',body:`
    import React from '${reactUrl}';
    import {BrowserRouter} from '${routerUrl}';
    import Provider from '/src/ui/PlatformProvider.tsx';
    import AutomationPage from '/src/pages/AutomationPage.tsx';
    export default function App(){return React.createElement(BrowserRouter,null,React.createElement(Provider,null,React.createElement('main',{style:{padding:20,minWidth:0}},React.createElement(AutomationPage))));}` }));
  await page.route('**/api/**',handler);
  const choose = async (name,text) => {
    await page.getByRole('combobox',{name,exact:true}).click();
    if(name.includes('项目'))await page.getByRole('combobox',{name,exact:true}).fill(text);
    await page.locator('.ant-select-dropdown:visible .ant-select-item-option-content').getByText(text,{exact:true}).click();
  };
  const goto=async()=>{await page.goto(origin+'/automation');await page.getByRole('heading',{name:'预标注与AI审核',exact:true}).waitFor();};
  const row=id=>(id.startsWith('job-')?page.locator('.ant-table-wrapper').first():page).getByRole('row').filter({hasText:id});
  try {
    await page.setViewportSize({width:1440,height:1000});await goto();
    await row('job-running').waitFor();
    if(!projectPages.includes(2))throw Error('Project options truncated at 100');
    checks.push('all project pages loaded');
    await page.locator('.ant-table-wrapper').first().locator('.ant-pagination-next').click();
    await page.locator('.ant-table-wrapper').first().locator('.ant-pagination-item-2.ant-pagination-item-active').waitFor();
    await page.locator('.ant-table-wrapper').first().locator('.ant-pagination-prev').click();
    if(!queries.some(q=>q.startsWith('/api/automation/jobs?')&&q.includes('page=2')))throw Error('Job pagination not sent');
    await page.getByRole('button',{name:'创建任务',exact:true}).click();
    await choose('任务项目','分页末尾项目 101');
    await page.getByRole('combobox',{name:'审核模型',exact:true}).click();
    await page.locator('.ant-select-dropdown:visible .ant-select-item-option-content').filter({hasText:'视觉审核服务（改名）'}).waitFor();
    if(await page.locator('.ant-select-dropdown:visible').getByText('其他描述协议',{exact:true}).count())throw Error('Unsupported caption protocol selectable');
    await page.keyboard.press('Escape');
    await page.getByRole('button',{name:'复制检测模型默认参数',exact:true}).click();
    if(await page.getByRole('spinbutton',{name:'检测置信度',exact:true}).inputValue()!=='0.31')throw Error('Model confidence not copied');
    await page.getByRole('spinbutton',{name:'每边外扩百分比',exact:true}).fill('35');
    await page.getByRole('textbox',{name:'资产 ID（可选）',exact:true}).fill('asset-1\nasset-2 asset-1');
    if(!await page.getByRole('dialog').locator('.ant-modal-footer').evaluate(el=>el.getBoundingClientRect().bottom<=innerHeight))throw Error('Create actions outside viewport');
    await page.screenshot({path:'output/playwright/automation-create.png',fullPage:true,animations:'disabled'});
    await page.getByRole('textbox',{name:'类别映射 JSON',exact:true}).fill('{broken');
    await page.getByRole('button',{name:'提交任务',exact:true}).click();
    await page.getByText('请输入字符串到字符串的 JSON 对象',{exact:true}).waitFor();
    if(writes.length)throw Error('Invalid mapping submitted');
    await page.getByRole('textbox',{name:'类别映射 JSON',exact:true}).fill('{"smoke":"fire"}');
    await page.getByRole('button',{name:'提交任务',exact:true}).click();
    await page.getByRole('dialog').waitFor({state:'hidden'});
    const payload=writes.find(w=>w.path==='/api/automation/jobs').payload;
    if(payload.project_id!=='p101'||payload.kind!=='preannotate'||payload.reviewer_id!=='reviewer'||payload.detector_id!=='detector'||payload.config.crop_expansion!==.35||payload.config.confidence!==.31||payload.config.iou!==.52||payload.config.auto_approve_confidence!==.9||!payload.config.auto_review||payload.asset_ids.join(',')!=='asset-1,asset-2')throw Error('Creation payload incorrect: '+JSON.stringify(payload));
    checks.push('model filtering, renamed chat reviewer, editable defaults, percent ratio, asset deduplication, invalid JSON guard');
    await page.getByRole('button',{name:'创建任务',exact:true}).click();
    await page.getByRole('button',{name:'关闭创建',exact:true}).click();
    if(writes.filter(w=>w.path==='/api/automation/jobs').length!==1)throw Error('Closing form submitted a job');
    await page.getByRole('button',{name:'创建任务',exact:true}).click();
    await choose('任务项目','检测项目 1');await choose('任务类型','AI审核（已有标注）');
    await page.getByRole('textbox',{name:'审核提示词',exact:true}).fill('检查全部项目类别有无遗漏');
    await page.getByRole('spinbutton',{name:'自动通过置信度',exact:true}).fill('0.95');
    await page.getByRole('button',{name:'提交任务',exact:true}).click();await page.getByRole('dialog').waitFor({state:'hidden'});
    const reviewPayload=writes.filter(w=>w.path==='/api/automation/jobs')[1].payload;
    if(reviewPayload.kind!=='review'||'asset_ids' in reviewPayload||'detector_id' in reviewPayload||reviewPayload.config.crop_expansion!==.2||reviewPayload.config.auto_approve_confidence!==.95||reviewPayload.config.prompt!=='检查全部项目类别有无遗漏')throw Error('Whole project review contract wrong');
    checks.push('closing form has no writes, whole project review omits asset scope and detector, modified threshold/prompt');
    await row('job-running').getByRole('button',{name:'取消任务',exact:true}).click();
    await page.getByRole('button',{name:'确认取消',exact:true}).click();
    await row('job-running').getByText('已取消',{exact:true}).waitFor();
    await row('job-failed').getByRole('button',{name:'重试任务',exact:true}).click();
    await row('job-failed').getByText('等待中',{exact:true}).waitFor();
    checks.push('cancel and retry calls refresh task state');
    await row('job-running').getByRole('button',{name:'查看明细',exact:true}).click();
    await page.getByRole('dialog').getByText('低分目标待确认',{exact:true}).first().waitFor();
    await page.getByRole('dialog').locator('.ant-pagination-next').click();
    await page.getByRole('dialog').getByText('低分目标待确认',{exact:true}).first().waitFor();
    await page.getByRole('button',{name:'证据详情',exact:true}).first().click();
    await page.getByText(/fixture image incomplete/).first().waitFor();
    await page.getByText(/fixture box uncertain/).first().waitFor();
    await page.getByText(/fixture negative needs admin/).first().waitFor();
    await page.getByText('整图完整性：不通过',{exact:true}).waitFor();
    await page.getByText('负样本结论：待确认',{exact:true}).waitFor();
    await page.getByRole('button',{name:'关闭证据',exact:true}).click();
    await choose('明细状态','失败');
    await page.getByRole('dialog').locator('.ant-empty-description').getByText('暂无数据',{exact:true}).waitFor();
    await page.getByRole('button',{name:'关闭明细',exact:true}).click();
    if(!queries.some(q=>q.startsWith('/api/automation/jobs/job-running/items?')&&q.includes('page=2'))||!queries.some(q=>q.includes('status=failed')&&q.includes('page=1')))throw Error('Item pagination/filter reset wrong');
    checks.push('paginated item status filter and complete evidence');
    await page.locator('.ant-table-wrapper').nth(1).locator('.ant-pagination-next').click();
    await row('asset-unknown').getByRole('button',{name:'预览审核',exact:true}).click();
    await page.getByText('候选框尚未保存；可以驳回移出队列，或打开标注工作台修正类别并保存。',{exact:true}).waitFor();
    if(await page.getByRole('button',{name:'批准',exact:true}).isEnabled())throw Error('Unsaved candidate may be approved');
    if(!(await page.getByRole('link',{name:'打开标注工作台',exact:true}).getAttribute('href')).startsWith('/annotate/asset-unknown?from=review'))throw Error('Unknown label lacks workspace link');
    await page.getByText('驳回仅移出旧候选，不修改新的人工标注',{exact:true}).waitFor();
    await page.getByRole('textbox',{name:'审核评论',exact:true}).fill('丢弃未保存候选');
    await page.getByRole('button',{name:'驳回',exact:true}).click();await page.getByRole('dialog').waitFor({state:'hidden'});
    await row('asset-stale').getByRole('button',{name:'预览审核',exact:true}).click();
    await page.getByText('标注版本已变化，请刷新队列后重新核对。',{exact:true}).waitFor();
    if(await page.getByRole('button',{name:'批准',exact:true}).isEnabled())throw Error('Version mismatch may be approved');
    await page.getByRole('textbox',{name:'审核评论',exact:true}).fill('丢弃过期候选');
    await page.getByRole('button',{name:'驳回',exact:true}).click();await page.getByRole('dialog').waitFor({state:'hidden'});
    await row('asset-deleted').getByRole('button',{name:'预览审核',exact:true}).click();
    await page.getByText('图片或标注读取失败，已禁用批准；可驳回移出旧候选。',{exact:true}).waitFor();
    if(await page.getByRole('button',{name:'批准',exact:true}).isEnabled())throw Error('Deleted original may be approved');
    await page.getByRole('textbox',{name:'审核评论',exact:true}).fill('丢弃已删除原图候选');
    await page.getByRole('button',{name:'驳回',exact:true}).click();await page.getByRole('dialog').waitFor({state:'hidden'});
    for(const id of ['item-unknown','item-stale','item-deleted'])if(!writes.some(w=>w.path.endsWith('/'+id+'/resolve')&&w.payload.approved===false))throw Error('Old candidate stuck: '+id);
    checks.push('unsaved, expired and deleted candidates block approval, allow rejection without touching annotations');
    await row('asset-1').getByRole('button',{name:'预览审核',exact:true}).click();
    await page.getByRole('img',{name:'审核原图',exact:true}).waitFor();
    await page.locator('svg[aria-label="审核框预览"] rect').waitFor();
    if(!await page.getByRole('dialog').locator('.ant-modal-footer').evaluate(el=>el.getBoundingClientRect().bottom<=innerHeight))throw Error('Review actions outside viewport');
    await page.screenshot({path:'output/playwright/automation-review.png',fullPage:true,animations:'disabled'});
    if(!annotationReads.includes('/api/annotations/annotation-1'))throw Error('Preview did not load the exact annotation');
    const link=await page.getByRole('link',{name:'打开标注工作台',exact:true}).getAttribute('href');
    if(!link.startsWith('/annotate/asset-1?from=review'))throw Error('Invalid workspace link');
    if(!link.includes('project_id=p1'))throw Error('All-project queue lost workspace project context');
    await page.getByRole('textbox',{name:'审核评论',exact:true}).fill('人工核对通过');
    await page.getByRole('button',{name:'批准',exact:true}).click();
    await page.getByRole('dialog').waitFor({state:'hidden'});
    const resolve=writes.find(w=>w.path.endsWith('/item-1/resolve')).payload;
    if(resolve.approved!==true||resolve.comment!=='人工核对通过')throw Error('Resolve payload wrong');
    checks.push('exact annotation version with boxes, original image, workspace link and approval comment');
    await row('asset-zero').getByRole('button',{name:'预览审核',exact:true}).click();
    await page.getByText('零框不能自动认定为负样本，请管理员核对原图后决定。',{exact:true}).waitFor();
    conflict=true;
    await page.getByRole('textbox',{name:'审核评论',exact:true}).fill('不能确认负样本');
    await page.getByRole('button',{name:'批准',exact:true}).click();
    await page.getByText('审核未保存，请刷新队列后重新核对标注版本。',{exact:true}).waitFor();
    if(!await page.getByRole('dialog').count())throw Error('Conflict dismissed review');
    await page.getByRole('button',{name:'驳回',exact:true}).click();await page.getByRole('dialog').waitFor({state:'hidden'});
    if(!writes.some(w=>w.payload?.approved===false&&w.payload.comment==='不能确认负样本'))throw Error('Reject missing after approval conflict');
    checks.push('zero box warning, approval conflict keeps comment and permits discarding obsolete candidate');
    conflict=false;queue.push({...item,id:'item-zero-confirm',asset_id:'asset-zero',annotation_id:'annotation-zero',result:{boxes:[]}});
    await page.getByRole('button',{name:'刷新待审',exact:true}).click();
    await row('asset-zero').getByRole('button',{name:'预览审核',exact:true}).click();
    await page.getByRole('textbox',{name:'审核评论',exact:true}).fill('管理员核对原图，确认负样本');
    await page.getByRole('button',{name:'批准',exact:true}).click();await page.getByRole('dialog').waitFor({state:'hidden'});
    if(!writes.some(w=>w.path.endsWith('/item-zero-confirm/resolve')&&w.payload.approved))throw Error('Admin cannot approve saved negative sample');
    checks.push('Qwen pass does not bypass human negative sample confirmation');
    await page.locator('.ant-modal-mask').waitFor({state:'hidden'});
    jobs=jobs.map(job=>job.id==='job-failed'?{...job,status:'completed'}:job);
    await page.getByRole('button',{name:'刷新任务',exact:true}).click();
    await row('job-failed').getByText('已完成',{exact:true}).waitFor();
    await page.waitForFunction(()=>document.querySelector('.ant-spin-spinning')===null);
    await page.clock.install();
    jobs=jobs.map(job=>job.id==='job-failed'?{...job,status:'pending'}:job);
    mode='hold';const before=listCalls;
    await page.getByRole('button',{name:'刷新任务',exact:true}).click();
    await page.waitForFunction(()=>document.querySelector('.ant-spin-spinning')!==null);
    await page.clock.runFor(10000);
    if(listCalls!==before+1)throw Error('Overlapping polls');
    mode='normal';release();
    await page.waitForFunction(()=>document.querySelector('.ant-spin-spinning')===null);
    const polled=listCalls, queueBefore=queueCalls;
    jobs=jobs.map(job=>job.status==='pending'?{...job,processed:job.processed+1,needs_review_count:job.needs_review_count+1}:job);
    await page.clock.runFor(3100);
    await page.waitForFunction(()=>document.querySelector('.ant-spin-spinning')===null);
    if(listCalls!==polled+1)throw Error('Active task did not poll after 3s');
    if(queueCalls<=queueBefore)throw Error('New review candidates did not refresh the admin queue');
    mode='hold';await page.getByRole('button',{name:'刷新任务',exact:true}).click();
    await page.waitForFunction(()=>document.querySelector('.ant-spin-spinning')!==null);
    mode='normal';await choose('筛选项目','分页末尾项目 101');
    await row('job-other').waitFor();release();await page.clock.runFor(4000);
    if(await row('job-running').count())throw Error('Late response replaced current project');
    const completedCalls=listCalls;await page.clock.runFor(10000);
    if(listCalls!==completedCalls)throw Error('Completed task kept polling');
    checks.push('3s completion-based polling, no overlap, new candidates refresh queue, stale project response ignored, terminal tasks stop polling');
    await page.clock.resume();
    await page.screenshot({path:'output/playwright/automation-admin.png',fullPage:true});
    role='annotator';const writesBefore=writes.length;await goto();await row('job-running').waitFor();
    if(await page.getByRole('button',{name:'创建任务',exact:true}).count()||await page.getByRole('button',{name:'取消任务',exact:true}).count()||await page.getByRole('button',{name:'重试任务',exact:true}).count()||await page.getByText('管理员待审队列',{exact:true}).count())throw Error('Ordinary user has admin controls');
    if(writes.length!==writesBefore)throw Error('Ordinary user writes');
    checks.push('ordinary user read-only without review queue calls');
    await page.setViewportSize({width:390,height:844});
    if(await page.locator('html').evaluate(el=>el.scrollWidth>el.clientWidth+1))throw Error('Narrow page overflow');
    await page.screenshot({path:'output/playwright/automation-narrow.png',fullPage:true});
    checks.push('narrow viewport');
    mode='error';await page.getByRole('button',{name:'刷新任务',exact:true}).click();
    await page.getByText('任务读取失败，请重试；当前显示可能已过期。',{exact:true}).waitFor();
    checks.push('request failure shown explicitly');
    if(unexpected.length)throw Error('Unexpected requests: '+unexpected.join(', '));
    if(!queries.some(q=>q.startsWith('/api/automation/review-queue?')&&q.includes('page=2')))throw Error('Queue pagination not sent');
    return {checks,writes,projectPages,annotationReads,listCalls,unexpected};
  } catch(error) {
    throw Error(error.message+'\nDOM: '+(await page.locator('body').innerText()).slice(-6500));
  } finally {
    if(release)release();await page.clock.resume().catch(()=>{});
    await page.goto('about:blank');await page.unroute('**/api/**',handler);await page.unroute(appPattern);
  }
}
