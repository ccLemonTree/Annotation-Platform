async page=>{
  await page.unrouteAll();page.setDefaultTimeout(20000);
  await page.setViewportSize({width:1440,height:1000});await page.goto('http://192.168.1.90:3000/assets');
  const groups=await page.evaluate(async()=>{
    const response=await fetch('/api/images/collections',{headers:{Authorization:'Bearer '+localStorage.getItem('token')}});
    if(!response.ok)throw Error('Cannot read collections');return response.json();
  });
  const owned=groups.filter(group=>group.name.startsWith('codex-lan-upload-'));
  if(owned.length!==1)throw Error('Expected exactly one owned fixture collection');
  const group=owned[0];
  await page.locator('.page-toolbar-actions').getByRole('button',{name:/上传图片/}).click();
  const dialog=page.getByRole('dialog',{name:'上传到图片库',exact:true});
  await dialog.getByRole('combobox',{name:'上传图片集名称'}).fill(group.name);
  await dialog.getByRole('spinbutton',{name:'本次上传长边阈值'}).fill('720');
  await dialog.getByLabel('上传图片或压缩包',{exact:true}).setInputFiles('F:/hzxf/数据管理平台/backend/backend/.backups/lan-upload/owned-fixture.zip');
  const start=()=>dialog.getByRole('button',{name:'开始 / 继续上传',exact:true});
  await start().click();await dialog.getByText(/新增 1，重复 0，低分辨率 1，无效 0/).waitFor();
  await dialog.getByRole('spinbutton',{name:'本次上传长边阈值'}).fill('0');
  await start().click();await dialog.getByText(/新增 1，重复 1，低分辨率 0，无效 0/).waitFor();
  await dialog.getByText('本次规则：不过滤尺寸',{exact:true}).waitFor();
  await dialog.getByRole('button',{name:'清空所选',exact:true}).click();
  const receipt=dialog.getByRole('row').filter({hasText:'owned-fixture.zip'}).filter({hasText:'不过滤尺寸'});
  await receipt.getByText('入库 1 张；已存在 1 张；尺寸过滤 0 张；无效 0 张；忽略 0 项',{exact:true}).waitFor();
  const viewed=page.waitForResponse(response=>response.url().includes('collection_id='+group.id));
  await receipt.getByRole('button',{name:'查看图片集',exact:true}).click();await viewed;
  await dialog.waitFor({state:'hidden'});
  await page.getByRole('cell',{name:'small.png',exact:true}).waitFor();
  await page.getByRole('cell',{name:'large.png',exact:true}).waitFor();
  await page.screenshot({path:'output/playwright/lan-upload-live-gallery.png'});
  return {realLANHTTP:true,actualZIPUploadedTwice:true,threshold720:{imported:1,filtered:1},override0:{imported:1,duplicates:1},visibleImages:['small.png','large.png'],scope:'Owned collection only; no business archive reimported'};
}
