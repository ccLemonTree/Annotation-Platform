async page=>{
  await page.unrouteAll({behavior:'ignoreErrors'});page.setDefaultTimeout(10000);
  const base='http://127.0.0.1:3000',projectId='11111111-1111-4111-8111-111111111111';
  const ids=['aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa','bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb','cccccccc-cccc-4ccc-8ccc-cccccccccccc'];
  const schema={target_label:'car',fields:[{key:'plate',name:'车牌',instruction:'只填写可见号码',options:[]},{key:'body_color',name:'车身颜色',instruction:'核对光照',options:[]},{key:'type',name:'车辆类型',instruction:'按外形',options:['轿车','SUV']}]};
  const project={id:projectId,project_name:'车辆属性标注',task_type:'caption',status:'active',label_schema:{object_attributes:schema}};
  const data={labels:[],description:'保留描述',attribute_schema:schema,attributes:{plate:{value:'苏L12345',reason:'模型可见'},body_color:{value:null,reason:'遮挡'},type:{value:'SUV',reason:'外形',probabilities:{SUV:.8,'轿车':.2}}}};
  const annotations=Object.fromEntries(ids.map(id=>[id,{id:'ann-'+id,asset_id:id,version:1,is_latest:true,review_status:'pending',annotation_source:'auto',annotation_data:structuredClone(data)}]));
  const writes=[],errors=[];let failSave=false,failReview=false,optionsReads=0;
  const check=(value,message)=>{if(!value)throw Error(message);};
  await page.addInitScript(()=>localStorage.setItem('token','isolated-annotation-usability'));
  page.on('pageerror',e=>errors.push(e.message));
  await page.route(base+'/api/**',async route=>{
    const req=route.request(),u=new URL(req.url()),p=u.pathname;
    if(p==='/api/auth/me')return route.fulfill({json:{id:2,tenant_id:1,username:'标注员',role:'user'}});
    if(p==='/api/analytics/activity')return route.fulfill({json:{}});
    if(p==='/api/projects/'+projectId)return route.fulfill({json:project});
    if(p.endsWith('/attribute-options')){optionsReads++;return route.fulfill({json:{body_color:['黑色','白色'],plate:[]}});}
    if(p==='/api/assets/collections')return route.fulfill({json:[]});
    if(p.startsWith('/api/assets/')){
      const id=p.split('/')[3],index=ids.indexOf(id);
      if(p.endsWith('/content'))return route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="1920" height="1080"><rect width="1920" height="1080" fill="#314c74"/><rect x="100" y="80" width="300" height="200" fill="#6e7b8d"/></svg>'});
      if(p.endsWith('/source'))return route.fulfill({json:{original_name:'完整原图.png',width:1920,height:1080,region:[100,80,400,280],content_url:p+'/content'}});
      if(p.endsWith('/neighbors'))return route.fulfill({headers:{'X-Asset-Attribute-Filter':'1'},json:{position:index+1,total:3,previous_id:ids[index-1]||null,next_id:ids[index+1]||null}});
      return route.fulfill({json:{id,project_id:projectId,original_name:'第'+(index+1)+'张裁剪图.png',width:400,height:250,source_type:'model_crop',annotation_status:'pending',device_info:{crop:{region:[10,20,310,220],root_region:[100,80,400,280]}}}});
    }
    if(p.endsWith('/analysis-models'))return route.fulfill({json:[]});
    if(p==='/api/annotations'&&req.method()==='GET'){
      const ann=annotations[u.searchParams.get('asset_id')];return route.fulfill({json:{items:[ann],total:1}});
    }
    if(p.startsWith('/api/annotations/ann-')){
      writes.push({path:p,body:req.postDataJSON()});
      if(p.endsWith('/review')){
        const ann=Object.values(annotations).find(a=>p.endsWith(a.id+'/review'));check(ann,'Review used stale annotation');
        if(failReview)return route.fulfill({status:409,json:{detail:'审核失败，请重试'}});
        check(ann.annotation_data.skipped===true&&req.postDataJSON().approved===true,'Review lost skipped state');
        ann.review_status='approved';return route.fulfill({json:ann});
      }
      if(failSave)return route.fulfill({status:409,json:{detail:'标注已更新，请重新核对'}});
      check(req.method()==='PUT','Unexpected review/create');
      const ann=Object.values(annotations).find(a=>p.endsWith(a.id));check(ann,'Missing annotation');
      ann.version++;ann.id+='-v'+ann.version;ann.annotation_data=req.postDataJSON().annotation_data;ann.annotation_source='manual';return route.fulfill({json:ann});
    }
    throw Error('Unexpected API '+req.method()+' '+p);
  });
  const field=name=>page.getByLabel(name+'属性值',{exact:true});
  const skip=()=>page.getByRole('checkbox',{name:'无法判断 / 跳过（不作负样本）',exact:true});
  await page.setViewportSize({width:1484,height:1000});await page.goto(base+'/annotate/'+ids[0]+'?attribute_key=type&attribute_state=analyzed');
  await field('车牌').waitFor();await page.getByRole('img',{name:'当前标注图片缩放区域',exact:true}).waitFor();
  const canvas=page.getByRole('img',{name:'当前标注图片缩放区域',exact:true});
  const initial=await canvas.getAttribute('viewBox');
  await page.getByRole('button',{name:'放大图片',exact:true}).click();check(await canvas.getAttribute('viewBox')!==initial,'Zoom button did nothing');
  await page.getByRole('button',{name:'适应图片',exact:true}).click();check(await canvas.getAttribute('viewBox')===initial,'Fit did not reset zoom');
  await canvas.hover();await page.mouse.wheel(0,-100);await page.waitForTimeout(100);check(await canvas.getAttribute('viewBox')!==initial,'Wheel did not zoom');
  await canvas.click();check(await canvas.isVisible(),'Image click unexpectedly changed view');
  await page.getByText('原图',{exact:true}).click();await page.getByRole('img',{name:'原图裁剪位置'}).waitFor();
  const rect=page.getByRole('img',{name:'原图裁剪位置'}).locator('g rect').first();check(await rect.getAttribute('x')==='100'&&await rect.getAttribute('y')==='80'&&await rect.getAttribute('width')==='300','Original rectangle uses local crop coordinates');
  await page.screenshot({path:'output/playwright/annotation-original-position.png',fullPage:true,animations:'disabled'});
  await page.getByText('标注切图',{exact:true}).click();await canvas.waitFor();
  await field('车身颜色').click();await page.locator('.ant-select-dropdown:visible .ant-select-item-option-content').filter({hasText:/^黑色$/}).click();
  check(await field('车身颜色').inputValue()==='黑色','Could not choose project suggestion');check(!await page.getByRole('checkbox',{name:'车身颜色无法判断',exact:true}).isChecked(),'Choosing value did not clear unknown');
  await field('车牌').fill('人工改值');await page.keyboard.press('r');check(writes.length===0,'Typing R triggered annotation save');
  await page.getByRole('button',{name:'预标注：苏L12345',exact:true}).click();check(await field('车牌').inputValue()==='苏L12345','Preannotation restore failed');
  await page.getByRole('button',{name:'保存标注',exact:true}).click();await page.waitForFunction(()=>[...document.querySelectorAll('button')].some(b=>b.textContent==='保存标注'&&b.disabled));
  check(writes[0].body.annotation_data.attributes.type.probabilities.SUV===.8,'Candidate edit changed model probabilities');
  const styles=await page.locator('.attribute-editor-row').evaluateAll(rows=>rows.map(row=>({bg:getComputedStyle(row).backgroundColor,padding:getComputedStyle(row).padding,box:row.getBoundingClientRect().toJSON()})));
  check(styles[0].bg!==styles[1].bg&&styles[0].box.bottom<styles[1].box.top,'Attributes have no visible separation');
  await page.screenshot({path:'output/playwright/annotation-usability-desktop.png',fullPage:true,animations:'disabled'});
  await skip().check();await page.getByRole('link',{name:'下一张',exact:true}).click();await page.waitForURL('**/annotate/'+ids[1]+'?**');await field('车牌').waitFor();
  check(writes.at(-1).body.annotation_data.skipped===true,'Next did not save skip');check(!writes.some(w=>w.path.endsWith('/review')),'Skip was approved');check(page.url().includes('attribute_key=type'),'Queue filter lost');
  check(optionsReads===1,'Suggestions refetched for every image');
  failSave=true;await skip().check();await page.getByRole('link',{name:'下一张',exact:true}).click();await page.locator('.ant-alert').getByText('标注已更新，请重新核对',{exact:true}).waitFor();
  check(page.url().includes(ids[1])&&await skip().isChecked(),'Failed skip save lost current image/draft');
  failSave=false;failReview=true;await skip().focus();await page.keyboard.press('r');await page.locator('.ant-alert').getByText('审核失败，请重试',{exact:true}).waitFor();
  check(page.url().includes(ids[1])&&await skip().isChecked(),'Failed review switched image or lost skip');
  check(annotations[ids[1]].annotation_data.skipped===true&&annotations[ids[1]].review_status==='pending','Failed review changed skip/status');
  const savesBeforeRetry=writes.filter(w=>!w.path.endsWith('/review')).length;
  failReview=false;await page.getByRole('button',{name:'保存并通过，下一张',exact:true}).click();await page.waitForURL('**/annotate/'+ids[2]+'?**');await field('车牌').waitFor();
  check(writes.filter(w=>!w.path.endsWith('/review')).length===savesBeforeRetry,'Retry created another annotation version');
  check(annotations[ids[1]].review_status==='approved'&&annotations[ids[1]].annotation_data.skipped,'Skip was not approved before next');
  await skip().check();await page.keyboard.press('r');await page.getByText('已通过，当前筛选已无下一张',{exact:true}).waitFor();
  check(annotations[ids[2]].annotation_data.skipped===true&&annotations[ids[2]].review_status==='approved','R did not save and approve last skip');
  check(writes.filter(w=>w.path.endsWith('/review')).length===3,'Unexpected review calls');
  await page.waitForFunction(()=>[...document.querySelectorAll('button')].some(b=>b.textContent==='保存标注'&&b.disabled));
  await skip().uncheck();await page.getByRole('button',{name:'填写目标属性',exact:true}).click();
  await page.setViewportSize({width:420,height:1000});await page.screenshot({path:'output/playwright/annotation-usability-narrow.png',fullPage:true,animations:'disabled'});
  check((await field('车牌').boundingBox()).height>20,'Narrow attributes are collapsed');
  check(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+1),'Narrow page overflows');
  check(errors.length===0,errors.join('\n'));
  return {checks:19,writes:writes.length,optionsReads,skippedApproved:true,reviewFailurePreservesImage:true,screenshots:['annotation-usability-desktop.png','annotation-original-position.png','annotation-usability-narrow.png']};
}
