async page=>{
  await page.unrouteAll();page.setDefaultTimeout(10000);
  const origin='http://127.0.0.1:3000',writes=[],errors=[];
  const source=await(await page.request.get(origin+'/src/pages/AnnotationWorkspace.tsx')).text();
  const react=source.match(/["'](\/node_modules\/\.vite\/deps\/react\.js\?[^"']+)["']/)[1],router=source.match(/["'](\/node_modules\/\.vite\/deps\/react-router-dom\.js\?[^"']+)["']/)[1];
  const project={id:'project-fixture',project_name:'切图验证项目',task_type:'detection',label_schema:{labels:['fire','smoke']}};
  const assets=Array.from({length:3},(_,i)=>({id:'asset-'+i,project_id:project.id,original_name:'image-'+i+'.png',width:i===1?320:640,height:480,source_type:'upload'}));
  let delayed=false,release,fail=false;
  const pending=new Promise(resolve=>{release=resolve;});
  await page.route('**/src/App.tsx*',route=>route.fulfill({contentType:'application/javascript',body:`import React from '${react}';import{createBrowserRouter,RouterProvider}from'${router}';import Provider from '/src/ui/PlatformProvider.tsx';import Workspace from '/src/pages/AnnotationWorkspace.tsx';const router=createBrowserRouter([{path:'/annotate/:assetId',element:React.createElement(Provider,null,React.createElement(Workspace))}]);export default function App(){return React.createElement(RouterProvider,{router});}`}));
  await page.route('**/api/**',async route=>{
    const req=route.request(),url=new URL(req.url()),path=url.pathname;
    if(!path.startsWith('/api/'))return route.continue();
    if(req.method()!=='GET'){writes.push(path);if(path.includes('/activity'))return route.fulfill({json:{}});return route.abort();}
    if(path==='/api/projects/'+project.id)return route.fulfill({json:project});
    if(path==='/api/model-services')return route.fulfill({json:[]});
    if(path.includes('/events'))return route.fulfill({json:[]});
    if(path==='/api/annotations'){
      const id=url.searchParams.get('asset_id'),index=Number(id.split('-')[1]);
      return route.fulfill({json:{items:[{id:'annotation-'+index,is_latest:true,version:1,review_status:'approved',annotation_data:{boxes:[{label:index===1?'smoke':'fire',x:10,y:10,width:20,height:20}]}}]}});
    }
    if(path.startsWith('/api/assets/')){
      const id=path.split('/')[3],asset=assets.find(a=>a.id===id),index=assets.indexOf(asset);
      if(path.endsWith('/neighbors'))return route.fulfill({json:{previous_id:assets[index-1]?.id,next_id:assets[index+1]?.id,position:index+1,total:3}});
      if(path.endsWith('/content')){
        if(id==='asset-1'){delayed=true;await pending;}
        if(id==='asset-2'&&fail)return route.fulfill({status:500,json:{detail:'fixture failure'}});
        return route.fulfill({contentType:'image/svg+xml',body:`<svg xmlns="http://www.w3.org/2000/svg" width="${asset.width}" height="480"><rect width="100%" height="100%" fill="${index===1?'green':'orange'}"/></svg>`});
      }
      return route.fulfill({json:asset});
    }
    throw Error('Unexpected '+path);
  });
  page.on('pageerror',e=>errors.push(e.message));
  try{
    await page.goto(origin+'/annotate/asset-0?label=fire');
    const canvas=()=>page.getByRole('img',{name:'检测画框区域',exact:true});await canvas().waitFor();
    const first=await canvas().locator('image').getAttribute('href');
    await page.getByRole('button',{name:'下一张',exact:true}).click();
    await page.waitForURL('**/asset-1?label=fire');
    for(let i=0;!delayed&&i<100;i++)await page.waitForTimeout(20);
    if(!delayed)throw Error('Delayed image was not requested');
    if(await canvas().count()!==1||await canvas().locator('image').getAttribute('href')!==first)throw Error('Canvas cleared before next image ready');
    if(!await page.getByRole('button',{name:'审核通过',exact:true}).isDisabled())throw Error('Old image remained editable during navigation');
    release();await page.getByText('切图验证项目 · image-1.png',{exact:true}).waitFor();
    await page.waitForFunction(first=>document.querySelector('.workspace-canvas image')?.getAttribute('href')!==first,first);
    if(await canvas().getAttribute('viewBox')!=='0 0 320 480')throw Error('New dimensions did not replace previous image dimensions');
    fail=true;await page.evaluate(()=>document.activeElement?.blur());await page.keyboard.press('ArrowRight');
    await page.getByText('工作台读取失败，请刷新或检查图片',{exact:true}).waitFor();
    if(await canvas().count()!==1)throw Error('Failed navigation discarded previous image');
    await page.screenshot({path:'output/playwright/annotation-transition.png',animations:'disabled'});
    if(errors.length)throw Error(errors.join(';'));
    return {checks:['Previous canvas remains visible during delayed next image','Editing disabled during pending transition','New image, dimensions and annotation switch together','Failed image load retains previous canvas'],annotationWrites:writes.filter(p=>!p.includes('/activity')).length};
  }finally{release();await page.goto('about:blank');await page.unrouteAll({behavior:'ignoreErrors'});}
}
