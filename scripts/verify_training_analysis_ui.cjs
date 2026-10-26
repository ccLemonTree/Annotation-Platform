async page=>{
  await page.unrouteAll();page.setDefaultTimeout(20000);
  const origin='http://192.168.1.90:3000',pattern=/^http:\/\/192\.168\.1\.90:3000\/api\//;
  const currentId='053025f1-a981-43f2-a5cd-57f116045d09',oldId='11111111-1111-4111-8111-111111111111';
  const current={id:currentId,name:'detr1016烟火训练7',kind:'rfdetr_2xlarge',status:'completed',warnings:[],files:[],
    metrics:[{name:'val/mAP_50_95',file:'metrics.csv',first:.4,last:.6,best:.65,best_epoch:1,best_step:null,last_epoch:2,last_step:null,direction:'higher',count:3,
      series:[{epoch:0,step:1,index:0,value:.4},{epoch:1,step:2,index:1,value:.65},{epoch:2,step:3,index:2,value:.6}]}]};
  const previous={...current,id:oldId,name:'历史基线',metrics:current.metrics.map(m=>({...m,first:.3,last:.5,best:.55,series:m.series.map((p,i)=>({...p,value:[.3,.55,.5][i]}))}))};
  const bundle={collected_at:new Date().toISOString(),current,previous:[previous],limitations:['测试证据，未运行独立验证。'],
    comparisons:[{run_id:oldId,name:'历史基线',comparable:false,warnings:['冻结数据快照不同，指标不能直接判定模型优劣。'],metrics:[{name:'val/mAP_50_95',file:'metrics.csv',current:.6,previous:.5,last_delta:.1,best_delta:.1,direction:'higher'}]}]};
  let report,fail=true,analysisCalls=0,evidenceCalls=0,unexpectedWrites=0,stage='init';const errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  await page.route(pattern,async route=>{
    const req=route.request(),path=new URL(req.url()).pathname,reply=(body,status=200)=>route.fulfill({json:body,status});
    if(path.endsWith('/comparison-candidates'))return reply({items:[{id:oldId,name:'历史基线',kind:'rfdetr_2xlarge',created_at:'2026-10-01T00:00:00Z',status:'completed'}],total:1});
    if(path.endsWith('/analysis/evidence')){evidenceCalls++;if(req.postDataJSON().compare_ids[0]!==oldId)throw Error('Missing comparison');return reply(bundle);}
    if(path.endsWith('/analysis')){
      if(req.method()==='GET')return reply(report?[{id:report.id,model:report.model,created_at:report.created_at}]:[]);
      analysisCalls++;const body=req.postDataJSON();
      if(body.compare_ids[0]!==oldId||!body.model_id||body.prompt!=='测试：逐项引用原始指标。')throw Error('Wrong analysis input');
      if(fail)return reply({detail:'模型请求失败，原始指标仍可查看'},502);
      report={id:'22222222-2222-4222-8222-222222222222',created_at:new Date().toISOString(),model:{name:'测试大模型',model_name:'fixture',revision:1},prompt:body.prompt,evidence:bundle,
        analysis:{summary:'对比测试结果：当前0.6，历史0.5。',findings:['val/mAP_50_95 差值0.1。'],recommendations:['统一验证集。'],limitations:['数据条件不一致，不能排名。']}};
      return reply(report);
    }
    if(path.includes('/analysis/'))return reply(report);
    if(req.method()!=='GET'){unexpectedWrites++;return route.abort();}
    return route.continue();
  });
  try{
    await page.setViewportSize({width:1440,height:1080});await page.goto(origin+'/training');
    const row=page.getByRole('row').filter({hasText:'detr1016烟火训练7'});
    await row.getByRole('button',{name:'效果分析与对比',exact:true}).click();
    await page.getByRole('combobox',{name:'历史对比模型',exact:true}).click();
    await page.getByText('历史基线 · rfdetr_2xlarge',{exact:false}).click();
    await page.getByRole('combobox',{name:'历史对比模型',exact:true}).press('Escape');
    await page.getByRole('button',{name:'读取指标并对比',exact:true}).click();
    await page.getByText('冻结数据快照不同，指标不能直接判定模型优劣。',{exact:true}).waitFor();
    await page.getByText('编辑本次训练分析提示词',{exact:true}).click();
    await page.getByRole('textbox',{name:'本次训练分析提示词',exact:true}).fill('测试：逐项引用原始指标。');
    stage='first analysis';await page.getByRole('button',{name:'大模型分析并保存报告',exact:true}).click();
    await page.getByText('模型请求失败，原始指标仍可查看',{exact:true}).waitFor();
    await page.getByRole('img',{name:'val/mAP_50_95 指标趋势，横轴epoch',exact:true}).waitFor();
    fail=false;stage='retry';await page.getByRole('button',{name:'大模型分析并保存报告',exact:true}).click();
    await page.getByText('对比测试结果：当前0.6，历史0.5。',{exact:true}).waitFor();
    await page.getByText('模型请求失败，原始指标仍可查看',{exact:true}).waitFor({state:'hidden'});
    const drawer=page.getByRole('dialog');
    await drawer.getByText('与 历史基线 对比',{exact:true}).scrollIntoViewIfNeeded();
    await drawer.screenshot({path:'../output/playwright/training-analysis-comparison.png',animations:'disabled'});
    await drawer.getByRole('button',{name:/关闭|Close/}).click();
    await row.getByRole('button',{name:'效果分析与对比',exact:true}).click();
    await page.getByText('对比测试结果：当前0.6，历史0.5。',{exact:true}).waitFor();
    if(analysisCalls!==2||evidenceCalls!==1||unexpectedWrites||errors.length)throw Error(JSON.stringify({analysisCalls,evidenceCalls,unexpectedWrites,errors}));
    return {analysisCalls,evidenceCalls,unexpectedWrites,checks:['Historical model chosen','Metrics and deltas shown','Editable prompt sent','LLM failure retains evidence','Retry saves report','Reopen loads persisted report'],scope:'Analysis writes intercepted; no business data modified'};
  }catch(error){await page.screenshot({path:'../output/playwright/training-analysis-comparison-error.png'});throw Error(stage+' '+error.message+' Buttons: '+JSON.stringify(await page.getByRole('button').allTextContents()));}
  finally{await page.goto('about:blank');await page.unroute(pattern);}
}
