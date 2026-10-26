async(page)=>{
  await page.setViewportSize({width:1440,height:1000});
  await page.goto('http://localhost:3000/assets');
  await page.getByRole('heading',{name:'图片管理',exact:true}).waitFor();
  await page.getByText('工厂',{exact:true}).first().waitFor();
  await page.getByText('高位摄像头',{exact:true}).first().waitFor();
  await page.waitForFunction(()=>{const images=[...document.querySelectorAll('tbody img')];return images.length>0&&images.every(image=>image.complete&&image.naturalWidth>0);});
  await page.screenshot({path:'output/playwright/image-scenes-library.png',animations:'disabled'});
  const target=page.getByRole('combobox',{name:'筛选图片集',exact:true});
  await target.fill('历史图片');await target.press('ArrowDown');await target.press('Enter');
  await page.getByRole('button',{name:'图片集设置',exact:true}).click();
  const modal=page.getByRole('dialog',{name:'图片集入库与场景设置',exact:true});
  const minimum=modal.getByRole('spinbutton',{name:'默认长边阈值',exact:true});
  await minimum.waitFor();
  if(await minimum.inputValue()!=='720')throw Error('Unexpected active resolution threshold');
  if(await modal.getByRole('textbox',{name:'模型服务地址',exact:true}).inputValue()!=='http://10.11.12.123:31030/v1')throw Error('Unexpected active model endpoint');
  await modal.getByText(/任意一条边大于等于阈值/).waitFor();
  await page.screenshot({path:'output/playwright/image-scenes-settings.png',animations:'disabled'});
  await modal.getByRole('button',{name:'关闭',exact:true}).click();
  return {scope:'read-only actual configuration and scene labels; no writes',screenshots:['image-scenes-library.png','image-scenes-settings.png']};
}
