async page=>{
  await page.unrouteAll({behavior:'ignoreErrors'});page.setDefaultTimeout(10000);
  const base='http://127.0.0.1:3000',projectId='11111111-1111-4111-8111-111111111111';
  const ids=['aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa','bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'];
  const schema={target_label:'car',fields:[{key:'color',name:'颜色',instruction:'核对',options:['白']}]};
  let mode='caption',contentFails=false,screenshotMode=false;const errors=[],writes=[];
  await page.addInitScript(()=>localStorage.setItem('token','isolated-image-view'));
  page.on('pageerror',error=>errors.push(error.message));
  await page.route(/^http:\/\/127\.0\.0\.1:3000\/api(?:\/|\?|$)/,async route=>{
    const request=route.request(),url=new URL(request.url()),path=url.pathname,method=request.method();
    if(path==='/api/auth/me')return route.fulfill({json:{id:1,tenant_id:1,username:'管理员',role:'admin'}});
    if(path==='/api/analytics/activity')return route.fulfill({json:{}});
    if(method==='PUT'&&path.startsWith('/api/annotations/ann-')){const body=request.postDataJSON();writes.push(body);return route.fulfill({json:{id:'saved',asset_id:ids[0],version:2,is_latest:true,review_status:'pending',annotation_data:body.annotation_data}});}
    if(method!=='GET')throw Error(`Unexpected write ${method} ${path}`);
    if(path==='/api/projects/'+projectId)return route.fulfill({json:{id:projectId,project_name:'视图记忆测试',task_type:mode,status:'active',label_schema:{labels:screenshotMode?['car','person']:['car'],...(mode==='caption'?{object_attributes:schema}:{})}}});
    if(path.endsWith('/attribute-options'))return route.fulfill({json:{}});
    if(path==='/api/assets/collections')return route.fulfill({json:[]});
    if(path==='/api/annotations'){
      const id=url.searchParams.get('asset_id'),data=mode==='caption'?{labels:[],description:'',attribute_schema:schema,attributes:{color:{value:'白',reason:'可见'}}}:{boxes:[{x:10,y:10,width:80,height:60,label:'car'},...(screenshotMode?[{x:180,y:100,width:100,height:90,label:'person'}]:[])],negative:false};
      return route.fulfill({json:{items:[{id:'ann-'+id,asset_id:id,version:1,is_latest:true,annotation_source:'manual',review_status:'pending',annotation_data:data},...(mode==='detection'?[{id:'old-'+id,asset_id:id,version:0,is_latest:false,annotation_source:'manual',review_status:'pending',annotation_data:data}]:[])],total:mode==='detection'?2:1}});
    }
    if(path.startsWith('/api/annotations/assets/')&&path.endsWith('/events'))return route.fulfill({json:[]});
    if(path.startsWith('/api/assets/')){
      const id=path.split('/')[3],index=ids.indexOf(id);
      if(index<0)throw Error('Unexpected asset '+path);
      if(path.endsWith('/content')&&contentFails&&!path.endsWith('/source/content'))return route.fulfill({status:503,json:{detail:'fixture'}});
      if(path.endsWith('/content'))return route.fulfill({contentType:'image/svg+xml',body:`<svg xmlns="http://www.w3.org/2000/svg" width="400" height="250"><rect width="400" height="250" fill="${screenshotMode?'#f3f4f6':'#314c74'}"/></svg>`});
      if(path.endsWith('/source'))return route.fulfill({json:{original_name:`原图-${index+1}.png`,width:1000,height:800,region:[100+index*100,80,400+index*100,280],content_url:`/api/assets/${id}/source/content`}});
      if(path.endsWith('/neighbors'))return route.fulfill({headers:{'X-Asset-Attribute-Filter':'1'},json:{position:index+1,total:2,previous_id:ids[index-1]||null,next_id:ids[index+1]||null}});
      return route.fulfill({json:{id,project_id:projectId,original_name:`切图-${index+1}.png`,width:400,height:250,source_type:'model_crop',annotation_status:'pending'}});
    }
    if(path.endsWith('/analysis-models'))return route.fulfill({json:[]});
    if(path==='/api/model-services'||path==='/api/models/services')return route.fulfill({json:[]});
    throw Error(`Unexpected API ${method} ${path}`);
  });
  const check=(ok,message)=>{if(!ok)throw Error(message);};
  mode='caption';contentFails=true;await page.goto(base+'/annotate/'+ids[0]);await page.evaluate(()=>sessionStorage.clear());await page.reload();
  await page.getByText('图片加载失败，请重试',{exact:true}).waitFor();await page.getByText('原图',{exact:true}).click();await page.getByRole('img',{name:'原图裁剪位置'}).waitFor();
  contentFails=false;mode='detection';await page.goto(base+'/annotate/'+ids[0]);await page.evaluate(()=>sessionStorage.clear());await page.reload();
  const canvas=page.getByRole('img',{name:'检测画框区域',exact:true});await canvas.waitFor();
  const add=page.getByRole('button',{name:'新增框',exact:true});await add.focus();await page.keyboard.press('Enter');
  check(await page.getByRole('button',{name:/2 · car/}).count()===1,'键盘新增框失败');
  check(await page.getByLabel('框x',{exact:true}).evaluate(el=>document.activeElement===el),'新增后未聚焦坐标');
  await page.getByRole('button',{name:'撤销标注操作'}).click();check(await page.getByRole('button',{name:/2 · car/}).count()===0,'撤销未移除新增框');
  await page.getByRole('button',{name:'重做标注操作'}).click();await page.getByRole('button',{name:/2 · car/}).click();
  await page.getByLabel('框x',{exact:true}).fill('80');await page.getByLabel('框y',{exact:true}).fill('90');
  await page.getByLabel('框width',{exact:true}).fill('120');await page.getByLabel('框height',{exact:true}).fill('60');
  await page.getByRole('button',{name:'撤销标注操作'}).click();await page.getByRole('button',{name:'重做标注操作'}).click();
  await page.getByRole('button',{name:'保存标注',exact:true}).click();
  check(writes.length===1&&JSON.stringify(writes[0].annotation_data.boxes[1])===JSON.stringify({x:80,y:90,width:120,height:60,label:'car'}),'键盘坐标保存有误');
  const box=canvas.locator('g').first();check(await box.locator('rect').count()>=3,'缺少双层对比描边');
  const text=box.locator('text');check(await text.getAttribute('stroke')==='#000'&&await text.getAttribute('fill')==='#fff','文字缺少高对比描边');
  await page.getByText('原图',{exact:true}).click();const source=page.getByRole('img',{name:'原图裁剪位置'});await source.waitFor();await page.getByRole('button',{name:'放大图片',exact:true}).click();
  const before=await source.getAttribute('viewBox'),b=await source.boundingBox(),client=await page.context().newCDPSession(page);
  const touch=(type,points)=>client.send('Input.dispatchTouchEvent',{type,touchPoints:points});
  await touch('touchStart',[{x:b.x+b.width*.5,y:b.y+b.height*.5}]);await touch('touchMove',[{x:b.x+b.width*.7,y:b.y+b.height*.5}]);await touch('touchEnd',[]);
  check(await source.getAttribute('viewBox')!==before,'原图触摸未平移');
  check(await add.isDisabled(),'原图视图允许新增框');
  const moved=await source.getAttribute('viewBox');await touch('touchStart',[{x:b.x+b.width*.5,y:b.y+b.height*.5}]);await touch('touchCancel',[]);
  await touch('touchStart',[{x:b.x+b.width*.5,y:b.y+b.height*.5}]);await touch('touchMove',[{x:b.x+b.width*.7,y:b.y+b.height*.5}]);await touch('touchEnd',[]);
  check(await source.getAttribute('viewBox')!==moved,'取消后触摸被卡住');
  const first={x:b.x+b.width*.5,y:b.y+b.height*.5,id:1},second={x:b.x+b.width*.6,y:b.y+b.height*.5,id:2};
  await touch('touchStart',[first]);await touch('touchStart',[first,second]);await touch('touchEnd',[]);
  const afterMulti=await source.getAttribute('viewBox');
  await touch('touchStart',[first]);await touch('touchMove',[{...first,x:first.x+40}]);await touch('touchEnd',[]);
  check(await source.getAttribute('viewBox')!==afterMulti,'多指中断后触摸被卡住');
  const afterMultiRecovery=await source.getAttribute('viewBox');
  await touch('touchStart',[first]);await page.evaluate(()=>window.dispatchEvent(new Event('blur')));await touch('touchEnd',[]);
  await touch('touchStart',[first]);await touch('touchMove',[{...first,x:first.x+40}]);await touch('touchEnd',[]);
  check(await source.getAttribute('viewBox')!==afterMultiRecovery,'失焦后触摸被卡住');
  await page.getByText('标注切图',{exact:true}).click();
  await page.getByRole('combobox',{name:'标注历史版本'}).click();await page.locator('.ant-select-dropdown:visible').getByText('v0 · 待审核',{exact:true}).click();
  check(await add.isDisabled(),'历史只读版本允许新增框');
  screenshotMode=true;await page.setViewportSize({width:1484,height:1000});await page.goto(base+'/annotate/'+ids[0]);await page.reload();
  await page.getByRole('button',{name:/2 · person/}).waitFor();await page.getByRole('button',{name:'新增框',exact:true}).waitFor();
  await page.screenshot({path:'output/playwright/impeccable-fixed-detection.png',fullPage:false,animations:'disabled'});
  if(errors.length)throw Error(errors.join('\n'));
  return {failureCanSwitch:true,keyboardCreateUndoRedoSave:true,contrastingBox:true,touchPanCancelRecovery:true};
}
