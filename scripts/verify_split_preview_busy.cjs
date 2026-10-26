async page=>{
  await page.unrouteAll();page.setDefaultTimeout(10000);
  const origin='http://127.0.0.1:3000';
  const source=await(await page.request.get(origin+'/src/components/ProjectExport.tsx')).text();
  const react=source.match(/["'](\/node_modules\/\.vite\/deps\/react\.js\?[^"']+)["']/)[1];
  const assets=[{id:'a',original_name:'a.png',width:8,height:8,source_type:'upload'}];
  let release,requests=0;const held=new Promise(resolve=>release=resolve);
  await page.route('**/src/App.tsx*',route=>route.fulfill({contentType:'application/javascript',body:`import React from '${react}';import Provider from '/src/ui/PlatformProvider.tsx';import Export from '/src/components/ProjectExport.tsx';export default function App(){return React.createElement(Provider,null,React.createElement(Export,{projectId:'fixture'}));}`}));
  await page.route('**/api/**',async route=>{
    const path=new URL(route.request().url()).pathname;
    if(!path.startsWith('/api/'))return route.continue();
    if(path==='/api/assets')return route.fulfill({json:{items:assets,total:1}});
    if(path==='/api/annotations')return route.fulfill({json:{items:[{asset_id:'a',version:1,review_status:'approved',annotation_data:{boxes:[{label:'fire'}]}}],total:1}});
    if(path.endsWith('/snapshots'))return route.fulfill({json:[]});
    if(path.endsWith('/distribution')||path.endsWith('/split-sources'))return route.fulfill({json:{}});
    if(path.endsWith('/splits/preview')){
      requests++;await held;
      return route.fulfill({json:{splits:{a:'train'},sources:{},groups:[['a']],missing_source_asset_ids:[],policy:{},images_by_split:{train:1},images_by_label_by_split:{train:{fire:1},val:{fire:0},test:{fire:0}},warnings:[],errors:[{code:'missing_split',message:'缺少独立验证图片'}],can_freeze:false}});
    }
    throw Error('Unexpected API '+path);
  });
  try{
    await page.goto(origin);await page.getByRole('button',{name:'导出 / 冻结数据集',exact:true}).click();
    const button=page.getByRole('button',{name:'预览当前划分',exact:true});await button.click();
    await page.getByRole('status').filter({hasText:'正在检查'}).waitFor();
    await page.getByText(/正在检查 1 张图片，已等待 [1-9]\d* 秒/).waitFor();
    if(await button.isEnabled())throw Error('Duplicate preview enabled');
    await page.screenshot({path:'output/playwright/split-preview-busy.png',fullPage:true});
    release();await page.getByText('缺少独立验证图片',{exact:true}).waitFor();
    await page.getByRole('status').filter({hasText:'正在检查'}).waitFor({state:'hidden'});
    await button.waitFor();if(!await button.isEnabled()||requests!==1)throw Error('Busy state did not recover');
    return {checks:['Visible elapsed waiting status','Duplicate request disabled','Report replaces waiting status and controls recover'],businessWrites:0};
  }finally{release();await page.goto('about:blank');await page.unrouteAll({behavior:'ignoreErrors'});}
}
