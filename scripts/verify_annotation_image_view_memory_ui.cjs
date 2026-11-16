async page=>{
  await page.unrouteAll({behavior:'ignoreErrors'});page.setDefaultTimeout(10000);
  const base='http://127.0.0.1:3000',projectId='11111111-1111-4111-8111-111111111111';
  const ids=['aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa','bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'];
  const schema={target_label:'car',fields:[{key:'color',name:'颜色',instruction:'核对',options:['白']}]};
  let mode='caption';const errors=[];
  await page.addInitScript(()=>localStorage.setItem('token','isolated-image-view'));
  page.on('pageerror',error=>errors.push(error.message));
  await page.route(/^http:\/\/127\.0\.0\.1:3000\/api(?:\/|\?|$)/,async route=>{
    const request=route.request(),url=new URL(request.url()),path=url.pathname,method=request.method();
    if(path==='/api/auth/me')return route.fulfill({json:{id:1,tenant_id:1,username:'管理员',role:'admin'}});
    if(path==='/api/analytics/activity')return route.fulfill({json:{}});
    if(method!=='GET')throw Error(`Unexpected write ${method} ${path}`);
    if(path==='/api/projects/'+projectId)return route.fulfill({json:{id:projectId,project_name:'视图记忆测试',task_type:mode,status:'active',label_schema:{labels:['car'],...(mode==='caption'?{object_attributes:schema}:{})}}});
    if(path.endsWith('/attribute-options'))return route.fulfill({json:{}});
    if(path==='/api/assets/collections')return route.fulfill({json:[]});
    if(path==='/api/annotations'){
      const id=url.searchParams.get('asset_id');return route.fulfill({json:{items:[{id:'ann-'+id,asset_id:id,version:1,is_latest:true,annotation_source:'manual',review_status:'pending',annotation_data:mode==='caption'?{labels:[],description:'',attribute_schema:schema,attributes:{color:{value:'白',reason:'可见'}}}:{boxes:[{x:10,y:10,width:80,height:60,label:'car'}],negative:false}}],total:1}});
    }
    if(path.startsWith('/api/annotations/assets/')&&path.endsWith('/events'))return route.fulfill({json:[]});
    if(path.startsWith('/api/assets/')){
      const id=path.split('/')[3],index=ids.indexOf(id);
      if(index<0)throw Error('Unexpected asset '+path);
      if(path.endsWith('/content'))return route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="400" height="250"><rect width="400" height="250" fill="#314c74"/></svg>'});
      if(path.endsWith('/source'))return route.fulfill({json:{original_name:`原图-${index+1}.png`,width:1000,height:800,region:[100+index*100,80,400+index*100,280],content_url:`/api/assets/${id}/content`}});
      if(path.endsWith('/neighbors'))return route.fulfill({headers:{'X-Asset-Attribute-Filter':'1'},json:{position:index+1,total:2,previous_id:ids[index-1]||null,next_id:ids[index+1]||null}});
      return route.fulfill({json:{id,project_id:projectId,original_name:`切图-${index+1}.png`,width:400,height:250,source_type:'model_crop',annotation_status:'pending'}});
    }
    if(path.endsWith('/analysis-models'))return route.fulfill({json:[]});
    if(path==='/api/model-services'||path==='/api/models/services')return route.fulfill({json:[]});
    throw Error(`Unexpected API ${method} ${path}`);
  });
  const view=()=>page.getByRole('img',{name:'原图裁剪位置',exact:true});
  const source=async index=>{
    await page.getByText(`原图-${index+1}.png`,{exact:false}).waitFor();await view().waitFor();
    const rect=view().locator('g rect').first();if(await rect.getAttribute('x')!==String(100+index*100))throw Error('原图区域未更新到当前图片');
  };
  const navigate=async (direction,index)=>{
    const role=mode==='detection'?'button':'link';await page.getByRole(role,{name:direction,exact:true}).click();
    await page.waitForURL('**/annotate/'+ids[index]+'*');
  };
  for(mode of ['caption','detection']){
    await page.goto(base+'/annotate/'+ids[0]);await page.evaluate(()=>sessionStorage.clear());await page.reload();
    await page.getByText('标注切图',{exact:true}).waitFor();await page.getByText('原图',{exact:true}).click();await source(0);
    await navigate('下一张',1);await source(1);
    await navigate('上一张',0);await source(0);
    await page.reload();await source(0);
    await page.getByText('标注切图',{exact:true}).click();
    await navigate('下一张',1);await page.getByText('标注切图',{exact:true}).waitFor();
    const canvas=page.getByRole('img',{name:mode==='detection'?'检测画框区域':'当前标注图片缩放区域',exact:true});await canvas.waitFor();
    if(await view().count())throw Error('切回标注切图后下一张又显示原图');
  }
  if(errors.length)throw Error(errors.join('\n'));
  return {caption:true,detection:true,sourceChangesWithAsset:true,refresh:true};
}
