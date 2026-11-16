async page=>{
  const base='http://127.0.0.1:3017',projectId='11111111-1111-4111-8111-111111111111';
  const first='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',second='bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',collection='cccccccc-cccc-4ccc-8ccc-cccccccccccc';
  const names=['上身服饰','上衣颜色','下身服饰','下装颜色','帽子','携带物品','身体朝向'];
  const schema={target_label:'person',fields:names.map((name,i)=>({key:'field'+i,name,instruction:'按清晰可见内容判断，不猜测',options:i===0?['夹克','短袖','衬衫']:i===3?['选项甲','选项乙','无法判断']:['选项甲','选项乙','选项丙']}))};
  const makeData=()=>({labels:['person'],description:'保留的描述',attribute_schema:schema,attributes:Object.fromEntries(schema.fields.map(f=>[f.key,{value:f.options[0],reason:'JEV最高概率候选，待人工核验',probabilities:{[f.options[0]]:.9,[f.options[1]]:.07,[f.options[2]]:.03}}]))});
  const makeAnn=id=>({id:'ann-'+id,asset_id:id,version:1,is_latest:true,review_status:'pending',annotation_source:'auto',annotation_data:makeData(),created_at:'2026-11-04T00:00:00Z'});
  const annotations={[first]:makeAnn(first),[second]:makeAnn(second)};
  const writes=[],neighbors=[],errors=[];let failSave=false,failReview=false,failNext=false,delayReview=0;
  await page.unrouteAll();page.setDefaultTimeout(6000);await page.setViewportSize({width:1440,height:1000});
  await page.addInitScript(()=>localStorage.setItem('token','isolated-attribute-review'));
  page.on('pageerror',e=>errors.push(e.message));
  await page.route(base+'/api/**',async route=>{
    const req=route.request(),u=new URL(req.url()),p=u.pathname,q=u.searchParams;
    const json=(data,status=200)=>route.fulfill({json:data,status,headers:{'X-Annotation-Review-Filter':'1','X-Asset-Collection-Filter':'1','X-Annotation-Label-Filter':'1'}});
    if(req.method()!=='GET'){
      const body=req.postDataJSON(),entry={path:p,body};writes.push(entry);
      const ann=Object.values(annotations).find(row=>p.includes(row.id));
      if(!ann)throw Error('Unexpected write '+p);
      if(p.endsWith('/review')){
        if(delayReview)await new Promise(resolve=>setTimeout(resolve,delayReview));
        if(failReview)return json({detail:'审核失败，请重试'},500);
        ann.review_status=body.approved?'approved':'rejected';return json(ann);
      }
      if(failSave)return json({detail:'保存冲突，请重新加载'},409);
      if(body.base_version!==ann.version)throw Error('Stale annotation version');
      ann.annotation_data=body.annotation_data;ann.version++;ann.id='saved-'+ann.asset_id+'-'+ann.version;ann.review_status='pending';ann.annotation_source='manual';return json(ann);
    }
    if(p==='/api/auth/me')return json({id:1,role:'admin',username:'fixture',tenant_id:1});
    if(p==='/api/projects/'+projectId)return json({id:projectId,project_name:'人员属性快速审核',task_type:'caption',label_schema:{labels:['person'],object_attributes:schema}});
    if(p==='/api/assets/collections')return json([{id:collection,name:'当前图片集'}]);
    if(p.endsWith('/content'))return route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="640" height="480"><rect width="640" height="480" fill="#73848c"/><text x="220" y="240" font-size="36">TARGET</text></svg>'});
    if(p.endsWith('/neighbors')){
      neighbors.push(u.href);const id=p.split('/')[3],ann=annotations[id];
      if(failNext&&ann.review_status==='approved')return json({detail:'下一张读取失败'},500);
      return json({position:id===first?1:2,total:2,matched:ann.review_status!=='approved',previous_id:id===first?null:first,next_id:id===first?second:null});
    }
    if(p.startsWith('/api/assets/'))return json({id:p.split('/')[3],project_id:projectId,original_name:'person.png',source_type:'model_crop',width:640,height:480});
    if(p==='/api/annotations')return json({items:[annotations[q.get('asset_id')]],total:1});
    return json({detail:'Unexpected read '+p},404);
  });
  const start=async()=>{
    const discard=dialog=>dialog.accept();page.once('dialog',discard);
    try{await page.goto(base+'/annotate/'+first+'?from=attributes&label=person&review_status=pending&collection_id='+collection);}finally{page.off('dialog',discard);}
  };
  const quick=()=>page.getByRole('button',{name:'保存并通过，下一张',exact:true});
  const submit=async step=>{try{await quick().click();}catch(error){throw Error(step+': '+error.message);}};
  await start();await page.getByText('上身服饰',{exact:true}).waitFor();
  const rowHeight=await page.getByText('上身服饰',{exact:true}).evaluate(e=>(e.closest('.attribute-editor-row')||e.closest('.ant-space')).getBoundingClientRect().height);
  await page.screenshot({path:'output/playwright/attribute-review-speed-desktop.png'});
  await page.getByRole('button',{name:'上身服饰选择短袖',exact:true}).click();
  if(rowHeight>155)throw Error('Attribute rows still too tall: '+rowHeight);
  if(await page.getByLabel('上身服饰判断原因',{exact:true}).isVisible())throw Error('Optional repeated model reason is still expanded');
  await page.getByRole('checkbox',{name:'上衣颜色无法判断',exact:true}).check();
  await page.getByLabel('上衣颜色判断原因',{exact:true}).fill('被遮挡');
  await page.getByRole('button',{name:'下装颜色选择无法判断',exact:true}).click();
  if(!await page.getByRole('checkbox',{name:'下装颜色无法判断',exact:true}).isChecked())throw Error('Unknown candidate must use the unknown state');
  await page.getByLabel('下装颜色判断原因',{exact:true}).fill('看不清颜色');
  await page.getByText('身体朝向',{exact:true}).scrollIntoViewIfNeeded();
  const buttonBox=await quick().boundingBox();if(!buttonBox||buttonBox.y+buttonBox.height>1000)throw Error('Review actions scrolled out of view');
  const image=await page.getByRole('img',{name:'person.png',exact:true}).boundingBox();if(!image||image.y<0||image.y+image.height>1000)throw Error('Image lost while checking last attribute');
  failSave=true;await submit('save failure');await page.getByText('保存冲突，请重新加载',{exact:true}).waitFor();
  if(writes.some(row=>row.path.endsWith('/review'))||!page.url().includes(first))throw Error('Failed save reviewed or navigated');
  failSave=false;failReview=true;await submit('save retry and review failure');await page.getByText('审核失败，请重试',{exact:true}).waitFor();
  if(!page.url().includes(first)||annotations[first].review_status!=='pending')throw Error('Failed review navigated');
  const saved=annotations[first].annotation_data;
  if(saved.attributes.field0.value!=='短袖'||saved.attributes.field1.value!==null||saved.attributes.field1.reason!=='被遮挡'||saved.attributes.field0.probabilities['夹克']!==.9)throw Error('Correction, unknown reason, or original probability lost');
  const saves=writes.filter(row=>!row.path.endsWith('/review')).length;
  failReview=false;failNext=true;await submit('review retry and navigation failure');await page.getByText('下一张读取失败',{exact:true}).first().waitFor();
  if(!page.url().includes(first)||annotations[first].review_status!=='approved')throw Error('Failed navigation lost successful review');
  failNext=false;await submit('navigation retry');await page.waitForURL('**/annotate/'+second+'?**');
  await page.getByText('v1 · 待审核',{exact:true}).waitFor();
  if(writes.filter(row=>!row.path.endsWith('/review')).length!==saves)throw Error('Retry created duplicate saved versions');
  for(const part of ['label=person','review_status=pending','collection_id='+collection])if(!page.url().includes(part)||!neighbors.at(-1).includes(part))throw Error('Filtered queue context lost');
  if(await page.getByRole('dialog',{name:'有未保存的标注'}).count())throw Error('Saved draft incorrectly blocked navigation');
  delayReview=200;
  await page.getByLabel('标注内容',{exact:true}).focus();await page.keyboard.press('r');await page.keyboard.press('r');
  await page.getByText('已通过，当前筛选已无下一张',{exact:true}).waitFor();
  if(writes.filter(row=>row.path.includes('ann-'+second)&&row.path.endsWith('/review')).length!==1)throw Error('Repeated review shortcut duplicated writes');
  await page.setViewportSize({width:390,height:844});
  await page.getByRole('checkbox',{name:'帽子无法判断',exact:true}).check();await page.getByLabel('帽子判断原因',{exact:true}).fill('帽子看不清');
  const mobile=await quick().boundingBox();if(!mobile||mobile.y<0||mobile.y+mobile.height>844)throw Error('Mobile review action is not visible');
  await page.waitForFunction(()=>!document.querySelector('.ant-message-notice'));
  await page.screenshot({path:'output/playwright/attribute-review-speed-mobile.png'});
  await page.getByRole('button',{name:'保存标注',exact:true}).click();await page.getByText('v2 · 待审核',{exact:true}).waitFor();
  if(errors.length)throw Error(errors.join('\n'));
  return {passed:true,row_height:rowHeight,compact_candidates:true,sticky_actions:true,save_review_next:true,failures_preserve_progress:true,no_duplicate_save_or_review:true,filtered_queue_preserved:true,probabilities_preserved:true,mobile:true,errors};
}
