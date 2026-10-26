async page=>{
  await page.unrouteAll();page.setDefaultTimeout(10000);
  const origin='http://127.0.0.1:3000';
  const source=await(await page.request.get(origin+'/src/components/ProjectExport.tsx')).text();
  const react=source.match(/["'](\/node_modules\/\.vite\/deps\/react\.js\?[^"']+)["']/)[1];
  const assets=[{id:'a',original_name:'a.png',width:8,height:8,source_type:'upload'}];
  let snapshots=[],posts=0,downloads=0;
  await page.route('**/src/App.tsx*',route=>route.fulfill({contentType:'application/javascript',body:`import React from '${react}';import Provider from '/src/ui/PlatformProvider.tsx';import Export from '/src/components/ProjectExport.tsx';export default function App(){return React.createElement(Provider,null,React.createElement(Export,{projectId:'fixture'}));}`}));
  await page.route('**/api/**',async route=>{
    const req=route.request(),path=new URL(req.url()).pathname;
    if(!path.startsWith('/api/'))return route.continue();
    if(path==='/api/assets')return route.fulfill({json:{items:assets,total:1}});
    if(path==='/api/annotations')return route.fulfill({json:{items:[{asset_id:'a',version:1,review_status:'approved',annotation_data:{boxes:[{label:'fire'}]}}],total:1}});
    if(path.endsWith('/snapshots')){
      if(req.method()==='POST'){
        posts++;const payload=req.postDataJSON();if(payload.splits.a!=='train'||payload.labels[0]!=='fire')throw Error('Wrong freeze payload');
        const snapshot={id:'snapshot-'+posts,format:'yolo',created_at:new Date().toISOString(),manifest:{assets,labels:['fire'],distribution:{}}};
        snapshots=[snapshot,...snapshots];return route.fulfill({json:snapshot});
      }
      return route.fulfill({json:snapshots});
    }
    if(path.endsWith('/download')){downloads++;return route.fulfill({contentType:'application/zip',body:'fixture zip'});}
    if(path.endsWith('/distribution')||path.endsWith('/split-sources'))return route.fulfill({json:{}});
    if(path.endsWith('/splits/preview'))return route.fulfill({json:{splits:{a:'train'},sources:{},groups:[['a']],missing_source_asset_ids:[],policy:{},images_by_split:{train:1},images_by_label_by_split:{train:{fire:1},val:{fire:0},test:{fire:0}},warnings:[],errors:[],can_freeze:true}});
    throw Error('Unexpected API '+path);
  });
  try{
    await page.goto(origin);await page.getByRole('button',{name:'导出 / 冻结数据集',exact:true}).click();
    const freeze=page.getByRole('button',{name:'仅冻结',exact:true});await freeze.waitFor();
    if(await freeze.isEnabled())throw Error('Freeze enabled without checked splits');
    await page.getByRole('button',{name:'预览当前划分',exact:true}).click();
    await freeze.click();await page.getByRole('button',{name:/下载 yolo/}).waitFor();
    if(posts!==1||downloads!==0)throw Error('Freeze-only triggered download or did not save');
    const event=page.waitForEvent('download');await page.getByRole('button',{name:/下载 yolo/}).click();await event;
    if(downloads!==1||posts!==1)throw Error('Later download created a new snapshot');
    const combined=page.waitForEvent('download');await page.getByRole('button',{name:'冻结并下载',exact:true}).click();await combined;
    await page.waitForFunction(()=>document.querySelectorAll('.ant-modal-body button').length>0);
    if(posts!==2||downloads!==2)throw Error('Freeze-and-download changed behavior');
    return {checks:['Freeze disabled until valid preview','Freeze-only saves and refreshes list without download','Existing snapshot can be downloaded later','Freeze-and-download still works'],businessWrites:0};
  }finally{await page.goto('about:blank');await page.unrouteAll({behavior:'ignoreErrors'});}
}
