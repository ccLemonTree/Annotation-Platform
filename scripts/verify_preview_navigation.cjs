async page=>{
  await page.unrouteAll();
  page.setDefaultTimeout(8000);
  const origin='http://127.0.0.1:3000',id='aaaaaaaa-0000-4000-8000-000000000002',checks=[],requests=[];
  const source=await(await page.request.get(origin+'/src/pages/ModelPreviewPage.tsx')).text();
  const react=source.match(/["'](\/node_modules\/\.vite\/deps\/react\.js\?[^"']+)["']/)[1];
  const router=source.match(/["'](\/node_modules\/\.vite\/deps\/react-router-dom\.js\?[^"']+)["']/)[1];
  const app='**/src/App.tsx*',api='**/api/**';
  const run={id,name:'Navigation fixture',model:{name:'Detector'},config:{inference_confidence:.25},status:'completed',processed:21,total:21,failed_count:0};
  const row=n=>({id:'item-'+n,original_name:'image-'+n+'.jpg',width:640,height:480,status:'completed',predictions:[],truth:[]});
  let delay=false,delayImage=false;
  await page.route(app,route=>route.fulfill({contentType:'application/javascript',body:`import React from '${react}';import {BrowserRouter} from '${router}';import Provider from '/src/ui/PlatformProvider.tsx';import Preview from '/src/pages/ModelPreviewPage.tsx';export default function App(){return React.createElement(Provider,null,React.createElement(BrowserRouter,null,React.createElement(Preview)));}`}));
  await page.route(api,async route=>{
    const req=route.request(),url=new URL(req.url()),path=url.pathname;
    if(!path.startsWith('/api/'))return route.continue();
    if(req.method()!=='GET')throw Error('Unexpected write '+path);
    requests.push(path+url.search);
    if(path==='/api/auth/me')return route.fulfill({json:{role:'admin'}});
    if(path==='/api/model-services')return route.fulfill({json:[]});
    if(path.endsWith('/config'))return route.fulfill({json:{max_images:5000,max_upload_size:100,max_image_size:100,max_expanded_size:100}});
    if(path==='/api/model-previews')return route.fulfill({json:{items:[run],total:1}});
    if(path.endsWith('/image')){
      if(delayImage){delayImage=false;await new Promise(resolve=>setTimeout(resolve,300));}
      return route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="640" height="480"/>'});
    }
    if(path==='/api/model-previews/'+id){
      const current=Number(url.searchParams.get('page'))||1;
      if(delay){delay=false;await new Promise(resolve=>setTimeout(resolve,300));}
      return route.fulfill({json:{...run,page:current,page_size:20,items:current===1?Array.from({length:20},(_,i)=>row(i+1)):[row(21)]}});
    }
    throw Error('Unexpected read '+path);
  });
  const selected=()=>page.locator('.model-preview-gallery .is-selected');
  const expectImage=async n=>{try{await page.waitForFunction(n=>document.querySelector('.model-preview-gallery .is-selected')?.textContent?.startsWith('image-'+n+'.jpg'),n);}catch(error){throw Error(`Expected image ${n}; selected ${await selected().textContent().catch(()=>'<none>')}; checks ${checks.join('; ')}; url ${page.url()}; body ${(await page.locator('body').innerText()).slice(-800)}`);}};
  const blur=()=>page.evaluate(()=>document.activeElement?.blur());
  const watchScroll=async()=>{
    await page.waitForFunction(()=>document.querySelector('.model-preview-image img')?.complete);
    await page.evaluate(()=>{window.scrollTo(0,document.documentElement.scrollHeight-innerHeight-10);window.scrollSamples=[scrollY];window.scrollTimer=setInterval(()=>window.scrollSamples.push(scrollY),10);});
  };
  const checkScroll=async()=>{
    await page.waitForFunction(()=>document.querySelector('.model-preview-image img')?.complete);
    const samples=await page.evaluate(()=>{clearInterval(window.scrollTimer);return window.scrollSamples;});
    if(samples[0]<100||Math.max(...samples)-Math.min(...samples)>2)throw Error('Image navigation moved the page vertically: '+JSON.stringify(samples));
  };
  try{
    await page.setViewportSize({width:1440,height:1000});await page.goto(origin+'/model-preview?run_id='+id);
    await expectImage(1);await page.keyboard.press('ArrowLeft');await expectImage(1);
    await page.keyboard.press('ArrowRight');await expectImage(2);await page.keyboard.press('ArrowLeft');await expectImage(1);
    checks.push('Left/right select adjacent images and stop at the first image');
    const input=page.getByRole('spinbutton',{name:'预览置信度',exact:true});await input.focus();await input.press('ArrowRight');await expectImage(1);await blur();
    const combo=page.getByRole('combobox',{name:'预览模型来源',exact:true});await combo.focus();await combo.press('ArrowRight');await expectImage(1);await combo.press('Escape');await blur();
    await page.keyboard.press('Control+ArrowRight');await expectImage(1);
    checks.push('Input/select focus and modifier shortcuts preserve editing');
    await page.setViewportSize({width:1440,height:600});await watchScroll();
    delayImage=true;await page.keyboard.press('ArrowRight');await expectImage(2);await checkScroll();
    checks.push('Switching a same-page image preserves vertical scroll during image loading');
    await page.getByRole('button',{name:/^image-20.jpg/}).click();await blur();
    await watchScroll();
    delay=true;await page.keyboard.press('ArrowRight');await page.keyboard.press('ArrowRight');await expectImage(21);
    await checkScroll();
    if(!page.url().includes('page=2'))throw Error('Did not cross to page 2');
    await page.keyboard.press('ArrowRight');await expectImage(21);
    await page.keyboard.press('ArrowLeft');await expectImage(20);
    checks.push('Page boundary loads the next first/previous last image; rapid keys and last boundary are safe');
    await page.getByRole('button',{name:'刷新结果',exact:true}).click();await expectImage(20);await blur();
    await page.keyboard.press('ArrowLeft');await expectImage(19);
    await page.getByRole('button',{name:'下一张图片',exact:true}).click();await expectImage(20);
    await page.getByRole('button',{name:'上一张图片',exact:true}).click();await expectImage(19);
    checks.push('Refresh retains selection and explicit previous/next controls work');
    await page.screenshot({path:'output/playwright/preview-navigation.png',animations:'disabled'});
    return {checks,requests:requests.length,businessWrites:0};
  }finally{await page.goto('about:blank');await page.unroute(api);await page.unroute(app);}
}
