async page => {
  page.setDefaultTimeout(7000);
  const origin='http://127.0.0.1:3000';
  const source=await (await page.request.get(origin+'/src/components/ImageUploadDialog.tsx')).text();
  const react=source.match(/["'](\/node_modules\/\.vite\/deps\/react\.js\?[^"']+)["']/)[1];
  const context=page.context();
  const config={chunk_size:8388608,max_file_size:10737418240,max_image_size:104857600,max_expanded_size:21474836480,max_entries:200000,retention_days:7};
  const job={id:'upload-a',filename:'archive-a.zip',size:16777216,uploaded_bytes:8388608,status:'uploading',collection_id:'shared',
    project_id:'project-a',can_manage:true,min_long_edge:720,result:{imported:0,duplicates:0,filtered:0,invalid:0,ignored:0,errors:[]}};
  let latest={...job};
  let requestScopes=[],created,association;
  await context.addInitScript(()=>localStorage.setItem('user',JSON.stringify({id:1,role:'admin'})));
  await context.route('**/src/App.tsx*',route=>route.fulfill({contentType:'application/javascript',
    body:`import React from '${react}';import Dialog from '/src/components/ImageUploadDialog.tsx';export default function App(){const projectId=new URLSearchParams(location.search).get('project');return React.createElement(Dialog,{open:true,initialName:'同一图片集',projectId,projectName:projectId,collections:[],onClose(){},onImported(){},onViewCollection(){}});}`}));
  await context.route(/^https?:\/\/[^/]+\/api\//,route=>{
    const request=route.request(), url=new URL(request.url());
    if(url.pathname==='/api/image-uploads/config')return route.fulfill({json:config});
    if(url.pathname==='/api/images/ingest-settings')return route.fulfill({json:{min_long_edge:720,classification_enabled:true}});
    if(url.pathname==='/api/image-uploads'&&request.method()==='GET'){
      const scope=url.searchParams.get('project_id');
      requestScopes.push(scope);
      return route.fulfill({json:scope==='project-a'?[latest]:[]});
    }
    if(url.pathname==='/api/image-uploads'&&request.method()==='POST'){
      created=request.postDataJSON();
      return route.fulfill({json:{...job,id:'finished',status:'completed'}});
    }
    if(url.pathname==='/api/image-uploads/finished/images')return route.fulfill({json:{image_ids:['uploaded-image'],total:1,page_size:1000}});
    if(url.pathname==='/api/images/associate'){
      association=request.postDataJSON();
      return route.fulfill({json:{asset_ids:['asset'],added_count:1,preannotation_queued:1}});
    }
    throw Error('Unexpected '+url.pathname);
  });
  await page.goto(origin+'/?project=project-a');
  const observer=await context.newPage();
  await observer.goto(origin+'/?project=project-a');
  const history=observer.getByRole('region',{name:'当前项目上传记录',exact:true});
  await history.getByRole('row',{name:/archive-a\.zip/}).waitFor();
  await history.getByText('16.0 MiB',{exact:true}).waitFor();
  await history.getByText('50%',{exact:true}).waitFor();
  if(await observer.getByText('本机上传队列',{exact:true}).count())throw Error('Empty local file table obstructs server progress');
  await observer.getByText(/场景分类关闭/).waitFor();
  latest={...job,uploaded_bytes:12582912,can_manage:false};
  await history.getByText('75%',{exact:true}).waitFor({timeout:5500});
  await history.getByText('仅查看',{exact:true}).waitFor();
  await observer.goto(origin+'/?project=project-b');
  await observer.getByText(/暂无按项目保存的上传记录/).waitFor();
  if(await observer.getByRole('row',{name:/archive-a\.zip/}).count())throw Error('Unrelated project leaked an upload');
  await page.getByLabel('上传图片或压缩包',{exact:true}).setInputFiles({name:'resume.jpg',mimeType:'image/jpeg',buffer:Buffer.from('fixture')});
  await page.getByRole('button',{name:'开始 / 继续上传',exact:true}).click();
  await page.getByText('所选文件已处理完成，可继续添加文件或关闭窗口',{exact:true}).waitFor();
  if(created?.project_id!=='project-a'||!requestScopes.includes('project-b'))throw Error('Upload create/list omitted its project');
  await page.getByText(/1 张已加入预标注/).waitFor();
  if(association?.upload_id!=='finished')throw Error('Project association omitted its upload receipt');
  await observer.close();
  return {sameAccountSecondTab:true,serverSize:'16.0 MiB',serverProgress:[50,75],projectIsolation:true,defaultClassification:false};
}
