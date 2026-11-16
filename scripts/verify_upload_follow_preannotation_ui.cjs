async page=>{
  page.setDefaultTimeout(9000);
  const origin=process.env.UPLOAD_TEST_ORIGIN||'http://127.0.0.1:3000';
  const source=await(await page.request.get(origin+'/src/pages/AssetsPage.tsx')).text();
  const react=source.match(/["'](\/node_modules\/\.vite\/deps\/react\.js\?[^"']+)["']/)[1];
  const router=source.match(/["'](\/node_modules\/\.vite\/deps\/react-router-dom\.js\?[^"']+)["']/)[1];
  const requests=[];
  let enabled=false,automationCreate,workCreate;
  let work={id:'work',operation:'detect_attributes',model_id:'fixture',revision:1,total:1,counts:{completed:1},model_name:'检测模型'};
  const attributeStage={total:1,counts:{running:1}};
  const active={...work,id:'active',model_name:'正在处理的模型',pipeline:{detection:{total:1,counts:work.counts},attributes:attributeStage}};
  const cancellations=[];
  const deletions=[];
  const hidden=new Set();
  const models=[
    {id:'detector',name:'检测夹具',key:'detector',kind:'detection',enabled:true,protocol:'model_service',labels:['person'],revision:1},
    {id:'vision',name:'视觉夹具',key:'vision',kind:'caption',enabled:true,protocol:'openai_chat',labels:[],revision:1},
  ];
  await page.route('**/src/App.tsx*',route=>route.fulfill({contentType:'application/javascript',body:`
    import React from '${react}';import{BrowserRouter}from'${router}';
    import AutomationJobDetails from '/src/components/automation/AutomationJobDetails.tsx';
    import AutomationJobForm from '/src/components/automation/AutomationJobForm.tsx';
    import ProjectAnnotationTools from '/src/components/ProjectAnnotationTools.tsx';
    import PlatformProvider from '/src/ui/PlatformProvider.tsx';
    export default function App(){return React.createElement(PlatformProvider,null,React.createElement(Fixture));}
    function Fixture(){
      const [follow,setFollow]=React.useState(false);
      const detection={id:'detect',kind:'preannotate',status:'completed',follow_uploads:follow,processed:1,total:1,config:{}};
      const project={id:'fixture',project_name:'目标属性测试',task_type:'caption',label_schema:{object_attributes:{target_label:'person',fields:[]}}};
      if(location.search==='?create=automation')return React.createElement(AutomationJobForm,{projects:[{...project,task_type:'detection',label_schema:{labels:['person']}}],models:${JSON.stringify(models)},projectId:'fixture',fixedProject:true,onClose(){},onCreated(){}});
      if(location.search==='?create=work')project.label_schema={};
      if(location.search==='?create=crop'){project.task_type='detection';project.label_schema={annotation_scope:'crops'};}
      if(location.search)return React.createElement(BrowserRouter,null,React.createElement(ProjectAnnotationTools,{project,onRefresh(){}}));
      return React.createElement(AutomationJobDetails,{job:detection,onClose(){},onFollowSaved:setFollow});
    }`}));
  await page.route(/^https?:\/\/[^/]+\/api\//,route=>{
    const req=route.request(),url=new URL(req.url()),path=url.pathname;
    if(req.method()==='POST'&&path==='/api/automation/jobs'){
      automationCreate=req.postDataJSON();return route.fulfill({json:{id:'created',follow_uploads:true}});
    }
    if(req.method()==='POST'&&path.endsWith('/work/jobs')){
      hidden.delete('work');
      workCreate=req.postDataJSON();enabled=workCreate.follow_uploads;work={...work,operation:workCreate.operation,crop_from_originals:true};
      return route.fulfill({json:{...work,follow_uploads:enabled}});
    }
    if(path.endsWith('/follow-uploads')){
      enabled=req.postDataJSON().enabled;requests.push([path,enabled]);return route.fulfill({json:{follow_uploads:enabled}});
    }
    if(req.method()==='POST'&&path.endsWith('/cancel')){
      cancellations.push(path);attributeStage.counts={cancelled:1};return route.fulfill({json:active});
    }
    if(req.method()==='DELETE'&&path.endsWith('/work/jobs/work')){
      deletions.push(path);hidden.add('work');return route.fulfill({status:204});
    }
    if(path.endsWith('/work/jobs')){
      if(page.url().endsWith('?queue=long')){
        const count=url.searchParams.get('page')==='2'?1:20;
        return route.fulfill({json:Array.from({length:count},(_,i)=>({...work,
          id:'batch-'+i,operation:'crop',model_name:count===1?'第二页模型':'批量模型 '+i,
          target_labels:['person','car','truck'],counts:{pending:4000,running:5,completed:995},total:5000}))});
      }
      return route.fulfill({json:[{...work,follow_uploads:enabled},active].filter(row=>!hidden.has(row.id))});
    }
    if(path.endsWith('/work/jobs/work'))return route.fulfill({json:{...work,follow_uploads:enabled,items:[],page:1}});
    if(path==='/api/model-services')return route.fulfill({json:models});
    if(path==='/api/model-services/prompt-defaults')return route.fulfill({json:{}});
    if(path==='/api/assets')return route.fulfill({headers:{'x-asset-crop-depth-filter':'1','x-asset-crop-batch-filter':'1'},
      json:{items:[{id:'source',project_id:'fixture',original_name:'fixture.jpg',width:800,height:600,source_type:'upload'}],total:1}});
    if(path==='/api/assets/source/content')return route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64"/>'});
    if(path.endsWith('/items'))return route.fulfill({json:{items:[],total:0}});
    throw Error('Unexpected fixture API '+path);
  });
  await page.goto(origin);
  const toggle=checked=>page.getByRole('checkbox',{name:'自动加入新上传图片',exact:true,checked});
  await toggle(false).click();await toggle(true).waitFor();
  await toggle(true).click();await toggle(false).waitFor();
  await page.goto(origin+'/?work=1');
  await page.getByRole('tab',{name:'后台处理队列'}).click();
  const doneRow=page.getByRole('row',{name:/检测模型/});
  const activeRow=page.getByRole('row',{name:/正在处理的模型/});
  const drawer=page.getByRole('dialog',{name:/批次明细/});
  await doneRow.getByRole('button',{name:'查看明细',exact:true}).click();
  await drawer.waitFor();
  await toggle(false).click();
  await page.getByText('接收新上传',{exact:true}).waitFor();
  if(!await drawer.getByRole('button',{name:'删除任务'}).isDisabled())throw Error('Following batch can be deleted');
  await toggle(true).click();
  await page.getByText('接收新上传',{exact:true}).waitFor({state:'hidden'});
  const expected=[['/api/automation/jobs/detect/follow-uploads',true],['/api/automation/jobs/detect/follow-uploads',false],
    ['/api/projects/fixture/work/jobs/work/follow-uploads',true],['/api/projects/fixture/work/jobs/work/follow-uploads',false]];
  if(JSON.stringify(requests)!==JSON.stringify(expected))throw Error(JSON.stringify(requests));
  await page.keyboard.press('Escape');
  await drawer.waitFor({state:'hidden'});
  const cancel=activeRow.getByRole('button',{name:'取消任务',exact:true});
  if(!await doneRow.getByRole('button',{name:'取消任务',exact:true}).isDisabled())throw Error('Completed batch can be cancelled');
  await cancel.click();
  await page.getByRole('button',{name:/^返\s*回$/}).click();
  if(cancellations.length)throw Error('Dismissing the confirmation cancelled the job');
  await cancel.click();
  await page.getByRole('button',{name:'确认取消',exact:true}).click();
  await activeRow.getByText(/已取消 1/).waitFor();
  if(!await cancel.isDisabled())throw Error('Cancelled batch can still be cancelled');
  if(JSON.stringify(cancellations)!==JSON.stringify(['/api/projects/fixture/work/jobs/active/cancel']))throw Error(JSON.stringify(cancellations));
  await doneRow.getByRole('button',{name:'继续切图',exact:true}).click();
  await page.getByRole('combobox',{name:'输入切图批次'}).waitFor();
  await page.getByRole('tab',{name:'后台处理队列'}).click();
  await doneRow.getByRole('button',{name:'查看明细',exact:true}).click();
  await drawer.getByRole('button',{name:'删除任务',exact:true}).click();
  await page.getByRole('button',{name:/^返\s*回$/}).click();
  if(deletions.length)throw Error('Dismissing the confirmation deleted the batch');
  await drawer.getByRole('button',{name:'删除任务',exact:true}).click();
  await page.getByRole('button',{name:'确认删除',exact:true}).click();
  await drawer.waitFor({state:'hidden'});
  await doneRow.waitFor({state:'hidden'});
  if(JSON.stringify(deletions)!==JSON.stringify(['/api/projects/fixture/work/jobs/work']))throw Error(JSON.stringify(deletions));
  await page.goto(origin+'/?create=automation');
  await page.getByRole('combobox',{name:'任务类型'}).click();
  await page.getByText('AI审核（已有标注）',{exact:true}).last().click();
  await toggle(false).click();
  const reviewPost=page.waitForResponse(res=>res.request().method()==='POST'&&new URL(res.url()).pathname==='/api/automation/jobs');
  await page.getByRole('button',{name:'提交任务',exact:true}).click();
  await reviewPost;
  if(automationCreate?.kind!=='review'||automationCreate.follow_uploads!==true)throw Error('Review creation omitted auto-follow');
  await page.goto(origin+'/?create=automation');
  await toggle(false).click();
  const detectionPost=page.waitForRequest(req=>req.method()==='POST'&&new URL(req.url()).pathname==='/api/automation/jobs');
  await page.getByRole('button',{name:'提交任务',exact:true}).click();await detectionPost;
  if(automationCreate?.follow_uploads!==true)throw Error('Detection creation omitted auto-follow');
  await page.goto(origin+'/?create=work');
  await page.getByRole('combobox',{name:'处理模型'}).click();
  await page.getByText('视觉夹具 · r1',{exact:true}).last().click();
  await page.getByRole('row',{name:/fixture\.jpg/}).getByRole('checkbox').check();
  await toggle(false).click();
  await page.getByRole('button',{name:/生成描述 · 已选 1 张/}).click();
  await page.getByText('接收新上传',{exact:true}).waitFor();
  if(workCreate?.follow_uploads!==true||workCreate.operation!=='caption')throw Error('Description creation omitted auto-follow');
  await page.goto(origin+'/?create=crop');
  const cropInput=page.getByRole('combobox',{name:'切图输入类型'});
  await cropInput.click();
  await page.getByText('按裁剪轮次选择',{exact:true}).last().click();
  if(!await toggle(false).isDisabled())throw Error('Later crop round accepts uploaded originals');
  await cropInput.click();
  await page.getByText('从原图开始',{exact:true}).last().click();
  await page.getByRole('combobox',{name:'处理模型'}).click();
  await page.getByText('检测夹具 · r1',{exact:true}).last().click();
  await page.getByRole('row',{name:/fixture\.jpg/}).getByRole('checkbox').check();
  await toggle(false).click();
  await page.getByRole('button',{name:/开始第 1 轮切图 · 已选 1 张/}).click();
  await page.getByText('接收新上传',{exact:true}).waitFor();
  if(workCreate?.follow_uploads!==true||workCreate.operation!=='crop')throw Error('Crop creation omitted auto-follow');
  await toggle(true).click();
  await page.getByText('接收新上传',{exact:true}).waitFor({state:'hidden'});
  await page.setViewportSize({width:1440,height:900});
  await page.goto(origin+'/?queue=long');
  await page.getByRole('tab',{name:'后台处理队列'}).click();
  const firstBatch=page.getByRole('row',{name:/批量模型 0/});
  await firstBatch.waitFor();
  const body=page.locator('.ant-table-body').filter({has:firstBatch});
  const height=await body.evaluate(el=>({visible:el.clientHeight,total:el.scrollHeight}));
  if(height.visible>420||height.total<=height.visible)throw Error('Long queue exceeds its scroll region');
  if(process.env.QUEUE_SCREENSHOT)await page.screenshot({path:process.env.QUEUE_SCREENSHOT});
  await page.getByRole('button',{name:'下一页',exact:true}).click();
  await page.getByRole('row',{name:/第二页模型/}).waitFor();
  await page.getByRole('button',{name:'上一页',exact:true}).click();
  await firstBatch.waitFor();
  await page.setViewportSize({width:390,height:844});
  const overflows=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth);
  if(overflows)throw Error('Queue overflows the page at 390px');
  if(process.env.QUEUE_SCREENSHOT)await page.screenshot({path:process.env.QUEUE_SCREENSHOT.replace('.png','-narrow.png')});
  return {detection:true,attributeAnalysis:true,toggles:requests.length,creationFollow:true,reviewFollow:true,cropFollow:true,
    rowCancellation:true,queueDeletion:true,queueHeight:height.visible,pagination:true,narrow:true};
}
