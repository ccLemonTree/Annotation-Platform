async page=>{
  const base='http://127.0.0.1:3017',projectId='11111111-1111-4111-8111-111111111111';
  const first='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',second='bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb';
  const schema={target_label:'car',fields:Array.from({length:7},(_,i)=>({key:'field'+i,name:'属性'+(i+1),instruction:'只能填写可见内容',options:[]}))};
  const data={labels:['car'],description:'已有描述',attribute_schema:schema,attributes:Object.fromEntries(schema.fields.map(f=>[f.key,{value:null,reason:'遮挡'}]))};
  let classification=false,annotation={id:'annotation-fixture',version:1,is_latest:true,review_status:'pending',annotation_data:data,created_at:'2026-11-04T00:00:00Z'};
  const writes=[],errors=[];page.on('pageerror',e=>errors.push(e.message));page.setDefaultTimeout(7000);
  await page.addInitScript(()=>localStorage.setItem('token','isolated-image-workspace'));
  await page.route(base+'/api/**',async route=>{
    const request=route.request(),path=new URL(request.url()).pathname,json=data=>route.fulfill({json:data});
    if(request.method()!=='GET'){
      writes.push(request.postDataJSON());
      if(path==='/api/annotations/'+annotation.id){annotation={...annotation,version:annotation.version+1,annotation_data:request.postDataJSON().annotation_data};return json(annotation);}
      throw Error('Unexpected write '+path);
    }
    if(path==='/api/auth/me')return json({id:1,username:'fixture',role:'admin',tenant_id:1});
    if(path==='/api/assets/collections')return json([]);
    if(path==='/api/projects/'+projectId)return json({id:projectId,project_name:'车辆属性',task_type:classification?'classification':'caption',label_schema:{labels:['car','person'],classification_mode:'multi',...(!classification?{object_attributes:schema}:{})}});
    if(path.endsWith('/content'))return route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360"><rect width="640" height="360" fill="#85b3ad"/><text x="240" y="180" font-size="32">TARGET</text></svg>'});
    if(path.endsWith('/neighbors'))return json({previous_id:path.includes(first)?null:first,next_id:path.includes(first)?second:null,position:path.includes(first)?1:2,total:2});
    if(path.startsWith('/api/assets/'))return json({id:path.split('/')[3],project_id:projectId,original_name:'target.png',source_type:'model_crop',width:640,height:360});
    if(path==='/api/annotations')return json({items:classification?[]:[annotation],total:classification?0:1});
    return route.fulfill({status:404,json:{detail:'Unexpected path '+path}});
  });
  await page.setViewportSize({width:1440,height:900});await page.goto(base+'/annotate/'+first+'?from=attributes');
  await page.getByText('目标属性 · car',{exact:true}).waitFor();
  await page.getByText('第 1 / 2 张',{exact:true}).waitFor();
  if(await page.getByRole('checkbox',{name:'car',exact:true}).isVisible())throw Error('Optional classification distracts from attributes');
  const image=page.getByRole('img',{name:'target.png',exact:true}),last=page.getByText('属性7',{exact:true});
  const imageOnScreen=async()=>{const b=await image.boundingBox(),v=page.viewportSize();if(!b||b.width<100||b.height<60||b.y<0||b.y+b.height>v.height)throw Error('Image not visible while editing last attribute');};
  await last.scrollIntoViewIfNeeded();await imageOnScreen();
  await page.getByRole('button',{name:'保存标注',exact:true}).scrollIntoViewIfNeeded();await imageOnScreen();
  await page.screenshot({path:'output/playwright/image-attribute-workspace-desktop.png'});
  await page.getByText('图片分类与描述（可选）',{exact:true}).click();
  await page.getByText('多标签表示同一张图片可选择多个类别，与目标属性分别记录。',{exact:true}).waitFor();
  await page.getByRole('checkbox',{name:'person',exact:true}).check();
  await page.getByRole('button',{name:'保存标注',exact:true}).click();await page.getByText('v2 · 待审核',{exact:true}).waitFor();
  if(writes.length!==1||writes[0].annotation_data.labels.join(',')!=='car,person'||writes[0].annotation_data.description!=='已有描述'||writes[0].annotation_data.attributes.field6.reason!=='遮挡')throw Error('Optional fields or attributes lost during save');
  await page.getByRole('link',{name:'下一张',exact:true}).click();await page.getByText('第 2 / 2 张',{exact:true}).waitFor();
  await page.setViewportSize({width:390,height:844});
  await last.scrollIntoViewIfNeeded();await imageOnScreen();await page.screenshot({path:'output/playwright/image-attribute-workspace-mobile.png'});
  classification=true;await page.goto(base+'/annotate/'+first);await page.getByRole('checkbox',{name:'car',exact:true}).waitFor();
  await page.getByText('图片分类 · 可多选',{exact:true}).waitFor();
  if(errors.length)throw Error(JSON.stringify(errors));
  return {passed:true,checks:['desktop image retained at last attribute and save','position and next-image update','optional classification and description preserve data','mobile image retained','ordinary classification stays expanded'],business_writes:0,fixture_writes:writes.length,errors};
}
