async page => {
  await page.unrouteAll();await page.emulateMedia({reducedMotion:'no-preference'});
  const writes=[],errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.addInitScript({path:'F:/hzxf/数据管理平台/backend/backend/.backups/vector-placement/browser-auth.js'});
  await page.route(/^http:\/\/localhost:3000\/api\//,route=>{
    if(route.request().method()!=='GET'){writes.push(route.request().url());return route.fulfill({status:405,json:{detail:'Read only check'}});}
    return route.continue();
  });
  await page.goto('http://localhost:3000/annotations');
  const project=await page.evaluate(()=>window.__vectorPlacement.project_id);
  await page.goto('http://localhost:3000/annotations?project_id='+project);
  await page.getByRole('button',{name:'开始标注',exact:true}).waitFor();
  if(await page.getByText('向量检索与匹配标注',{exact:true}).count())throw Error('Matching controls remain on management');
  await page.setViewportSize({width:1440,height:1000});await page.screenshot({path:'output/playwright/vector-management-live.png'});
  await page.getByRole('button',{name:'开始标注',exact:true}).click();await page.waitForURL('**/annotate/project/'+project);
  await page.getByRole('heading',{name:'向量标注工作台',exact:true}).waitFor();
  await page.getByText('图片描述标注',{exact:true}).last().waitFor();
  if(await page.getByRole('combobox',{name:'检索向量模型',exact:true}).count())throw Error('Description mode must not require embeddings');
  if(await page.getByRole('combobox',{name:'处理方式',exact:true}).count())throw Error('Preparation controls leaked into workspace');
  await page.waitForFunction(()=>{const images=[...document.querySelectorAll('.ant-table img')];return images.length>0&&images.every(image=>image.complete&&image.naturalWidth>0)&&!document.querySelector('.ant-spin-blur');});
  await page.screenshot({path:'output/playwright/vector-workspace-live-desktop.png'});
  await page.reload();await page.getByText('图片描述标注',{exact:true}).last().waitFor();
  await page.setViewportSize({width:390,height:844});await page.waitForFunction(()=>document.documentElement.scrollWidth<=innerWidth);
  await page.screenshot({path:'output/playwright/vector-workspace-live-mobile.png'});
  const scope=await page.evaluate(async project=>{
    const headers={Authorization:'Bearer '+localStorage.getItem('token')};
    const get=async path=>{const r=await fetch(path,{headers});if(!r.ok)throw Error(path+' '+r.status);return r.json();};
    const [images,queue,originals]=await Promise.all([get('/api/images?source_type=model_crop'),get('/api/assets?project_id='+project+'&scope=annotation'),get('/api/assets?project_id='+project+'&image_kind=originals')]);
    if(images.total!==0||queue.items.some(a=>a.source_type!=='model_crop'||a.image_id))throw Error('Crop privacy or annotation scope violated');
    if(originals.items.length){const response=await fetch('/api/assets/'+originals.items[0].id+'/neighbors',{headers});if(response.status!==409)throw Error('Original entered the crop annotation queue');}
    return {hiddenLibraryCrops:images.total,privateCropQueue:queue.total,sourceOriginals:originals.total};
  },project);
  await page.getByRole('radiogroup',{name:'标注方式',exact:true}).getByText('文搜图标注',{exact:true}).click();
  await page.getByRole('combobox',{name:'检索向量模型',exact:true}).waitFor();
  await page.getByRole('radiogroup',{name:'标注方式',exact:true}).getByText('以图搜图标注',{exact:true}).click();
  await page.getByRole('button',{name:'上传查询图片',exact:true}).waitFor();
  await page.getByRole('button',{name:'导出标注数据',exact:true}).click();
  const modal=page.getByRole('dialog',{name:'导出训练标注'});
  await modal.getByRole('combobox',{name:'训练数据格式',exact:true}).click();await page.locator('.ant-select-dropdown:visible').getByText('大模型 SFT · messages + images',{exact:true}).click();
  await modal.getByRole('textbox',{name:'SFT训练指令',exact:true}).waitFor();
  await modal.getByRole('combobox',{name:'训练数据格式',exact:true}).click();await page.locator('.ant-select-dropdown:visible').getByText('向量 InfoNCE',{exact:true}).click();
  await modal.getByRole('combobox',{name:'训练数据来源',exact:true}).click();await page.locator('.ant-select-dropdown:visible').getByText('检索匹配判定（正确 / 错误）',{exact:true}).click();
  await page.locator('.ant-select-dropdown:visible').waitFor({state:'hidden'});
  await page.waitForFunction(()=>document.documentElement.scrollWidth<=innerWidth);await page.screenshot({path:'output/playwright/swift-export-live-mobile.png'});
  await modal.getByRole('button',{name:/^取\s*消$/}).click();
  await page.getByRole('link',{name:'返回项目管理',exact:true}).click();await page.getByRole('button',{name:'开始标注',exact:true}).waitFor();
  if(errors.length||writes.length)throw Error(JSON.stringify({errors,writes}));
  return {dedicatedWorkspace:true,plainDescriptionWithoutVectors:true,trainingExportFormats:true,managementHasNoMatching:true,directRefresh:true,mobileWidth:390,scope,writes:0,pageErrors:errors};
}
