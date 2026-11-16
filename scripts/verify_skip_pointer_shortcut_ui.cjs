async page=>{
  await page.unrouteAll({behavior:'ignoreErrors'});page.setDefaultTimeout(6000);
  const base='http://127.0.0.1:3000',projectId='11111111-1111-4111-8111-111111111111';
  const ids=['aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa','bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb','cccccccc-cccc-4ccc-8ccc-cccccccccccc'];
  const schema={target_label:'car',fields:[{key:'color',name:'颜色',instruction:'清晰可见颜色',options:[]}]};
  const project={id:projectId,project_name:'鼠标跳过快捷键验证',task_type:'caption',status:'active',label_schema:{object_attributes:schema}};
  const original={labels:[],description:'',attribute_schema:schema,attributes:{color:{value:'白色',reason:'可见'}}};
  const annotations=Object.fromEntries(ids.map(id=>[id,{id:'ann-'+id,asset_id:id,version:1,is_latest:true,review_status:'pending',annotation_source:'auto',annotation_data:structuredClone(original)}]));
  const writes=[],errors=[],diagnostics=[];let failSave=false,failReview=false;
  const check=(value,message)=>{if(!value)throw Error(message);};
  await page.addInitScript(()=>localStorage.setItem('token','isolated-skip-pointer-test'));
  page.on('pageerror',error=>errors.push(error.message));
  await page.route(base+'/api/**',async route=>{
    const req=route.request(),url=new URL(req.url()),path=url.pathname;
    if(path==='/api/auth/me')return route.fulfill({json:{id:2,tenant_id:1,username:'标注员',role:'user'}});
    if(path==='/api/analytics/activity')return route.fulfill({json:{}});
    if(path==='/api/projects/'+projectId)return route.fulfill({json:project});
    if(path.endsWith('/attribute-options'))return route.fulfill({json:{color:['白色','黑色']}});
    if(path==='/api/assets/collections')return route.fulfill({json:[]});
    if(path.startsWith('/api/assets/')){
      const id=path.split('/')[3],index=ids.indexOf(id);
      if(path.endsWith('/content'))return route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="640" height="480"><rect width="640" height="480" fill="#28354a"/></svg>'});
      if(path.endsWith('/neighbors'))return route.fulfill({json:{position:index+1,total:3,previous_id:ids[index-1]||null,next_id:ids[index+1]||null}});
      return route.fulfill({json:{id,project_id:projectId,original_name:'车辆.png',width:640,height:480,source_type:'upload',annotation_status:'pending'}});
    }
    if(path.endsWith('/analysis-models'))return route.fulfill({json:[]});
    if(path==='/api/annotations'&&req.method()==='GET')return route.fulfill({json:{items:[annotations[url.searchParams.get('asset_id')]],total:1}});
    if(path.startsWith('/api/annotations/ann-')){
      writes.push({path,body:req.postDataJSON()});
      const review=path.endsWith('/review'),ann=Object.values(annotations).find(row=>path.endsWith(row.id+(review?'/review':'')));
      check(ann,'请求使用过期标注');
      if(review){
        if(failReview)return route.fulfill({status:409,json:{detail:'审核失败，保留当前图'}});
        check(ann.annotation_data.skipped&&req.postDataJSON().approved,'审核丢失跳过状态');
        ann.review_status='approved';return route.fulfill({json:ann});
      }
      if(failSave)return route.fulfill({status:409,json:{detail:'保存失败，保留当前图'}});
      check(req.method()==='PUT','意外标注操作');ann.version++;ann.id+='-v'+ann.version;ann.annotation_data=req.postDataJSON().annotation_data;ann.annotation_source='manual';return route.fulfill({json:ann});
    }
    throw Error('意外接口 '+req.method()+' '+path);
  });
  const skip=()=>page.getByRole('checkbox',{name:'无法判断 / 跳过（不作负样本）',exact:true});
  const stable=locator=>locator.evaluate(element=>new Promise(resolve=>{
    let previous='',frames=0;
    const frame=()=>{const box=element.getBoundingClientRect(),current=[box.x,box.y,box.width,box.height].map(value=>value.toFixed(2)).join(',');frames=current===previous?frames+1:0;previous=current;if(frames>=3)resolve();else requestAnimationFrame(frame);};
    requestAnimationFrame(frame);
  }));
  const settled=async()=>{await page.getByLabel('颜色属性值',{exact:true}).waitFor();const canvas=page.getByRole('img',{name:'当前标注图片缩放区域',exact:true});await canvas.waitFor();await stable(canvas);await stable(skip());};
  const inspect=()=>page.evaluate(()=>({active:document.activeElement?.outerHTML,overlays:[...document.querySelectorAll('[role="dialog"],.ant-image-preview,.ant-select-dropdown,.ant-popover,.ant-drawer')].filter(element=>element.getClientRects().length>0&&getComputedStyle(element).visibility!=='hidden').map(element=>element.outerHTML.slice(0,300))}));
  const pointerClick=async locator=>{await locator.click({trial:true});await stable(locator);const box=await locator.boundingBox();check(box,'点击目标不可见');await page.mouse.click(box.x+box.width/2,box.y+box.height/2);};
  await page.setViewportSize({width:1648,height:885});await page.goto(base+'/annotate/'+ids[0]);await settled();
  await pointerClick(skip());diagnostics.push(await inspect());check(await skip().isChecked(),'鼠标未勾选整图跳过');
  await page.keyboard.press('r');await page.waitForURL(url=>url.pathname==='/annotate/'+ids[1]);await settled();
  check(annotations[ids[0]].review_status==='approved'&&annotations[ids[0]].annotation_data.skipped,'点击复选框后 R 未保存并通过');
  await pointerClick(page.locator('.image-annotation-fields').getByText('无法判断 / 跳过（不作负样本）',{exact:true}));diagnostics.push(await inspect());
  check(await skip().isChecked(),'鼠标点击文字未勾选整图跳过');
  await page.keyboard.press('r');await page.waitForURL(url=>url.pathname==='/annotate/'+ids[2]);await settled();
  check(annotations[ids[1]].review_status==='approved'&&annotations[ids[1]].annotation_data.skipped,'点击文字后 R 未保存并通过');
  await pointerClick(skip());failSave=true;
  const beforeModifiers=writes.length;
  for(const flags of [{repeat:true},{ctrlKey:true},{metaKey:true},{shiftKey:true},{altKey:true}])await skip().evaluate((element,flags)=>element.dispatchEvent(new KeyboardEvent('keydown',{key:'Process',code:'KeyR',isComposing:true,bubbles:true,...flags})),flags);
  check(writes.length===beforeModifiers,'修饰键或重复按键误触跳过审核');
  await skip().evaluate(element=>element.dispatchEvent(new KeyboardEvent('keydown',{key:'Process',code:'KeyR',isComposing:true,bubbles:true})));
  await page.locator('.ant-alert').getByText('保存失败，保留当前图',{exact:true}).waitFor();
  check(page.url().includes(ids[2])&&await skip().isChecked(),'保存失败丢失当前图');
  failSave=false;failReview=true;await pointerClick(skip());await pointerClick(skip());await page.keyboard.press('r');await page.locator('.ant-alert').getByText('审核失败，保留当前图',{exact:true}).waitFor();
  check(page.url().includes(ids[2])&&await skip().isChecked(),'审核失败丢失当前图');
  failReview=false;await pointerClick(skip());await pointerClick(skip());await page.keyboard.press('r');await page.getByText('已通过，当前筛选已无下一张',{exact:true}).waitFor();
  check(annotations[ids[2]].review_status==='approved'&&annotations[ids[2]].annotation_data.skipped,'重试 R 未保存并通过');
  await skip().uncheck();await page.getByRole('button',{name:'填写目标属性',exact:true}).click();
  const field=page.getByLabel('颜色属性值',{exact:true});await field.fill('手工');const before=writes.length;await page.keyboard.press('r');
  await field.evaluate(element=>element.dispatchEvent(new KeyboardEvent('keydown',{key:'Process',code:'KeyR',isComposing:true,bubbles:true})));
  check(writes.length===before,'文字输入 R 误触审核');
  await page.getByRole('button',{name:'快捷键',exact:true}).click();await page.keyboard.press('r');check(writes.length===before,'弹窗 R 误触审核');
  await skip().evaluate(element=>element.dispatchEvent(new KeyboardEvent('keydown',{key:'Process',code:'KeyR',isComposing:true,bubbles:true})));
  check(writes.length===before,'弹窗打开时后台复选框实体 R 误触审核');
  check(errors.length===0,errors.join('\n'));
  return {checks:['real pointer checkbox click then R','real pointer label click then R','IME Process KeyR on whole image skip','repeat and modifier keys protected','save failure keeps image','review failure keeps image','retry keeps skipped marker and approves','text input and composing R protected','dialog R and background composing KeyR protected'],diagnostics,writes:writes.length};
}
