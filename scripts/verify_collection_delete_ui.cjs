async page=>{
  await page.unrouteAll();page.setDefaultTimeout(8000);
  const groupId='33333333-3333-4333-8333-333333333333',keepId='44444444-4444-4444-8444-444444444444',pid='22222222-2222-4222-8222-222222222222',iid='55555555-5555-4555-8555-555555555555';
  const groups=[{id:groupId,name:'待删图片集',image_count:2,ingest_config:{classification_enabled:false,scenes:[]}},{id:keepId,name:'保留图片集',image_count:1}];
  const plan={total_images:2,deleted_images:1,kept_images:1};
  let deleted=false,fail=true;const writes=[],errors=[],imageFilters=[];
  page.on('pageerror',e=>errors.push(e.message));
  const image={id:iid,project_id:pid,original_name:'shared-image.png',collection_names:['待删图片集','保留图片集'],file_size:100,width:50,height:100,source_type:'upload',created_at:'2026-10-19T01:00:00Z',scene_labels:[],scene_status:'unclassified'};
  await page.route(/^http:\/\/localhost:3000\/api\//,route=>{
    const request=route.request(),url=new URL(request.url()),path=url.pathname;
    const json=(body,status=200)=>route.fulfill({json:body,status});
    if(request.method()!=='GET'){
      writes.push({path,method:request.method()});
      if(path===`/api/images/collections/${groupId}`&&request.method()==='DELETE'){
        if(fail){fail=false;return json({detail:'此图片集有未完成的上传，请先完成上传或在上传窗口清除未完成记录'},409);}
        deleted=true;return json(plan);
      }
      throw Error('Unexpected mocked write '+path);
    }
    if(path==='/api/images/collections')return json(deleted?[groups[1]]:groups);
    if(path.endsWith('/delete-preview'))return json(plan);
    if(path==='/api/images'){imageFilters.push(url.searchParams.get('collection_id'));const items=[{...image,collection_names:deleted?['保留图片集']:image.collection_names},...deleted?[]:[{...image,id:'66666666-6666-4666-8666-666666666666',original_name:'exclusive.png',collection_names:['待删图片集']}]];return json({items,total:items.length});}
    if(path==='/api/projects')return json({items:[],total:0});
    if(path==='/api/projects/'+pid)return json({id:pid,project_name:'项目',task_type:'detection',label_schema:{labels:['person']}});
    if(path==='/api/assets')return json({items:[],total:0});
    if(path.endsWith('/content'))return route.fulfill({contentType:'image/png',body:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aC+sAAAAASUVORK5CYII=','base64')});
    return json({items:[],total:0});
  });
  await page.goto('http://localhost:3000/login');await page.evaluate(()=>{localStorage.setItem('token','isolated');localStorage.setItem('user',JSON.stringify({role:'admin',username:'验收'}));localStorage.setItem('image-upload-collection','待删图片集');});
  await page.setViewportSize({width:1440,height:1000});await page.goto('http://localhost:3000/assets');
  await page.getByRole('row').filter({hasText:'shared-image.png'}).waitFor();
  if(!await page.getByRole('button',{name:'删除图片集',exact:true}).isDisabled())throw Error('未选择图片集时应禁用');
  await page.getByRole('combobox',{name:'筛选图片集',exact:true}).click();
  await page.locator('.ant-select-dropdown:visible').getByText('待删图片集（2）',{exact:true}).click();
  await page.getByRole('button',{name:'删除图片集',exact:true}).click();
  const dialog=page.getByRole('dialog',{name:'删除图片集及独占图片？',exact:true});
  await dialog.getByText('共 2 张图片：预计删除 1 张独占图片，保留 1 张共享图片。',{exact:true}).waitFor();
  await dialog.getByRole('button',{name:/取\s*消/}).click();
  if(writes.length)throw Error('取消时发出了删除请求');
  await page.getByRole('button',{name:'删除图片集',exact:true}).click();
  await dialog.getByRole('button',{name:/确认删除图片集/}).click();
  await dialog.getByText('此图片集有未完成的上传，请先完成上传或在上传窗口清除未完成记录',{exact:true}).waitFor();
  if(deleted)throw Error('失败时错误清除了图片集');
  await dialog.getByRole('button',{name:'重新核对',exact:true}).click();
  await dialog.getByText('共 2 张图片：预计删除 1 张独占图片，保留 1 张共享图片。',{exact:true}).waitFor();
  await page.setViewportSize({width:390,height:844});
  await page.waitForFunction(()=>document.documentElement.scrollWidth<=innerWidth);
  await dialog.screenshot({path:'output/playwright/collection-delete-confirm-mobile.png'});
  await dialog.getByRole('button',{name:/确认删除图片集/}).click();
  await dialog.waitFor({state:'hidden'});
  await page.getByRole('row').filter({hasText:'shared-image.png'}).waitFor();
  if(await page.getByRole('row').filter({hasText:'exclusive.png'}).count())throw Error('独占图片仍显示在图库');
  await page.waitForFunction(()=>localStorage.getItem('image-upload-collection')===null);
  await page.getByText('请先选择图片集，再修改入库规则或发起分类。 同集按内容去重，场景随原图共享；重新分类会更新该图在所有图片集中的场景。',{exact:true}).waitFor();
  if(imageFilters.at(-1)!==null)throw Error('成功后未清除图片集筛选');
  if(!await page.getByRole('button',{name:'删除图片集',exact:true}).isDisabled())throw Error('已删除的图片集仍可操作');
  await page.goto('http://localhost:3000/projects/'+pid+'/images');
  await page.getByRole('navigation',{name:'项目导航',exact:true}).waitFor();
  if(await page.getByRole('button',{name:'删除图片集',exact:true}).count())throw Error('项目图片页错误显示图片集删除');
  if(errors.length||writes.length!==2)throw Error(JSON.stringify({errors,writes}));
  return {checks:['选择后可删、未选择禁用、项目页不显示','预览独占和共享数量、取消零写入、409保留筛选并可重新核对','成功清除筛选和旧上传集记忆、独占图移除而共享图保留','390px弹窗无横向溢出'],mockDeleteRequests:writes.length,realBusinessWrites:0,pageErrors:errors};
}
