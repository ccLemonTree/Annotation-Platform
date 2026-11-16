async page=>{
  const base='http://127.0.0.1:3017',pid='11111111-1111-4111-8111-111111111111',collection='cccccccc-cccc-4ccc-8ccc-cccccccccccc';
  const ids=['aaaaaaaa','bbbbbbbb','dddddddd','eeeeeeee','ffffffff'].map(prefix=>prefix+'-aaaa-4aaa-8aaa-aaaaaaaaaaaa');
  const schema={target_label:'car',fields:[{key:'color',name:'车身颜色',instruction:'只看车身',options:['白色','黑色']}]};
  const asset=id=>({id,project_id:pid,original_name:id+'.png',source_type:'model_crop',width:640,height:480});
  const annotations=Object.fromEntries(ids.slice(0,4).map((id,index)=>[id,{id:'ann-'+id,asset_id:id,version:1,is_latest:true,review_status:'pending',annotation_source:'manual',
    annotation_data:{labels:['car'],description:'',attribute_schema:schema,attributes:{color:{value:index===3?null:index===1?'黑色':'白色',reason:index===3?'遮挡':'人工核对'}}}}]));
  const reads=[],writes=[],errors=[];let failList=false,attributeHeader=true;
  const matches=q=>ids.filter(id=>{
    const ann=annotations[id],item=ann?.annotation_data.attributes?.[q.get('attribute_key')],state=q.get('attribute_state');
    if(q.get('label')&&!ann?.annotation_data.labels.includes(q.get('label')))return false;
    if(q.get('review_status')&&ann?.review_status!==q.get('review_status'))return false;
    if(q.get('attribute_value')&&item?.value!==q.get('attribute_value'))return false;
    if(state==='analyzed'&&!item||state==='known'&&item?.value==null||state==='unknown'&&(!item||item.value!==null)||state==='missing'&&item)return false;
    return true;
  });
  await page.unrouteAll();await page.setViewportSize({width:1440,height:1000});page.setDefaultTimeout(7000);
  await page.addInitScript(()=>localStorage.setItem('token','isolated-attribute-navigation'));page.on('pageerror',e=>errors.push(e.message));
  await page.route(base+'/api/**',async route=>{
    const r=route.request(),u=new URL(r.url()),p=u.pathname,q=u.searchParams;
    const json=(data,status=200)=>route.fulfill({json:data,status,headers:{'X-Annotation-Review-Filter':'1','X-Asset-Collection-Filter':'1','X-Annotation-Label-Filter':'1',...(attributeHeader?{'X-Asset-Attribute-Filter':'1'}:{})}});
    if(r.method()!=='GET'){
      const body=r.postDataJSON();writes.push({path:p,body});const ann=Object.values(annotations).find(a=>p.includes(a.id));if(!ann)throw Error('Unexpected write '+p);
      if(p.endsWith('/review'))ann.review_status=body.approved?'approved':'rejected';
      else{ann.annotation_data=body.annotation_data;ann.version++;ann.id='saved-'+ann.asset_id+'-'+ann.version;ann.review_status='pending';}
      return json(ann);
    }
    reads.push(u.href);
    if(p==='/api/auth/me')return json({id:1,role:'admin',username:'fixture',tenant_id:1});
    if(p==='/api/projects/'+pid)return json({id:pid,project_name:'车辆属性筛选',task_type:'caption',label_schema:{labels:['car'],object_attributes:schema}});
    if(p==='/api/assets/collections')return json([{id:collection,name:'测试图片集'}]);
    if(p==='/api/assets'){
      if(failList)return json({detail:'筛选请求失败'},500);
      const selected=matches(q);return json({items:selected.slice((Number(q.get('page')||1)-1),Number(q.get('page')||1)).map(asset),total:selected.length});
    }
    if(p.endsWith('/neighbors')){
      const id=p.split('/')[3],selected=matches(q),index=selected.indexOf(id),anchor=ids.indexOf(id);
      return json({position:index+1,total:selected.length,matched:index>=0,previous_id:index>=0?selected[index-1]||null:[...selected].reverse().find(v=>ids.indexOf(v)<anchor)||null,next_id:index>=0?selected[index+1]||null:selected.find(v=>ids.indexOf(v)>anchor)||null});
    }
    if(p.endsWith('/content'))return route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="640" height="480"><rect width="640" height="480" fill="#7a8793"/></svg>'});
    if(p.startsWith('/api/assets/'))return json(asset(p.split('/')[3]));
    if(p==='/api/annotations')return json({items:annotations[q.get('asset_id')]?[annotations[q.get('asset_id')]]:[],total:annotations[q.get('asset_id')]?1:0});
    return json({detail:'Unexpected '+p},404);
  });
  const select=async(name,label)=>{await page.getByRole('combobox',{name,exact:true}).click();await page.locator('.ant-select-dropdown:visible .ant-select-item-option-content').getByText(label,{exact:true}).click();};
  const value=()=>page.getByRole('combobox',{name:'属性值筛选',exact:true});
  await page.goto(base+'/annotate/'+ids[0]+'?from=review&project_id='+pid);
  await select('筛选目标属性','车身颜色');await page.waitForURL('**attribute_key=color**');
  await page.getByText('第 1 / 4 张',{exact:true}).waitFor();
  await select('筛选图片集','测试图片集');await page.waitForURL('**collection_id=**');await select('筛选标注标签','car');await page.waitForURL('**label=car**');await select('筛选审核状态','待审核');await page.waitForURL('**review_status=pending**');
  await value().fill('黑色');if(page.url().includes('attribute_value='))throw Error('Typing applied the value before confirmation');
  await value().press('Enter');await page.waitForURL('**/annotate/'+ids[1]+'?**attribute_value=**');
  await page.getByText('第 1 / 1 张',{exact:true}).waitFor();
  const current=page.url();await value().fill('蓝色');await value().press('Enter');await page.getByText('当前筛选没有匹配图片，已保留当前图片和筛选',{exact:true}).waitFor();
  if(page.url()!==current||await value().inputValue()!=='黑色')throw Error('Empty filter lost active value or image');
  failList=true;await value().fill('白色');await value().press('Enter');await page.getByText('筛选失败，已保留当前图片和筛选，请重试',{exact:true}).waitFor();failList=false;
  if(page.url()!==current||await value().inputValue()!=='黑色')throw Error('Failed filter changed conditions');
  attributeHeader=false;await value().fill('白色');await value().press('Enter');await page.getByText('筛选失败，已保留当前图片和筛选，请重试',{exact:true}).waitFor();attributeHeader=true;
  if(page.url()!==current)throw Error('Old backend silently ignored filter capability');
  await page.getByRole('button',{name:'车身颜色选择白色',exact:true}).click();
  if(!await page.getByRole('combobox',{name:'筛选目标属性',exact:true}).isDisabled()||!await value().isDisabled())throw Error('Dirty annotation allowed filter changes');
  await page.getByRole('button',{name:'保存并通过，下一张',exact:true}).click();await page.getByText('已通过，当前筛选已无下一张',{exact:true}).waitFor();
  await page.getByText('当前筛选已无匹配图片，可清除筛选查看全部',{exact:true}).waitFor();
  await value().fill('白色');await value().press('Enter');await page.waitForURL('**/annotate/'+ids[0]+'?**');
  await page.getByText('第 1 / 2 张',{exact:true}).waitFor();
  await page.getByRole('link',{name:'下一张',exact:true}).click();await page.waitForURL('**/annotate/'+ids[2]+'?**');
  await page.getByRole('spinbutton',{name:'跳转图片序号',exact:true}).fill('1');await page.getByRole('spinbutton',{name:'跳转图片序号',exact:true}).press('Enter');await page.waitForURL('**/annotate/'+ids[0]+'?**');
  await page.getByRole('button',{name:'车身颜色选择黑色',exact:true}).click();await page.getByRole('button',{name:'保存并通过，下一张',exact:true}).click();await page.waitForURL('**/annotate/'+ids[2]+'?**');
  for(const part of ['attribute_key=color','attribute_value=','review_status=pending','label=car','collection_id='+collection])if(!page.url().includes(part)||!reads.filter(url=>url.includes('/neighbors')).at(-1).includes(part))throw Error('Navigation dropped '+part);
  await page.getByText('v1 · 待审核',{exact:true}).waitFor();await page.getByRole('button',{name:'审核通过',exact:true}).click();
  await page.getByRole('button',{name:'清除筛选',exact:true}).click();
  await page.waitForFunction(()=>!location.search.includes('attribute_key'));
  for(const key of ['attribute_value','attribute_state','collection_id','review_status','label='])if(page.url().includes(key))throw Error('Clear left a stale filter '+key);
  await select('筛选目标属性','车身颜色');await select('筛选属性状态','无法判断');await page.waitForURL('**/annotate/'+ids[3]+'?**');
  await page.waitForFunction(()=>document.querySelector('input[aria-label="属性值筛选"]')?.disabled===true);
  if(!await value().isDisabled()||page.url().includes('attribute_value='))throw Error('Unknown filter retained incompatible value');
  await select('筛选属性状态','尚未分析此属性');await page.waitForURL('**/annotate/'+ids[4]+'?**');
  await page.reload();await page.getByText('第 1 / 1 张',{exact:true}).waitFor();
  await page.screenshot({path:'output/playwright/attribute-navigation-filter.png'});
  if(errors.length)throw Error(errors.join('\n'));
  return {passed:true,attribute_value_and_state:true,explicit_value_apply:true,empty_and_failure_preserve_filters:true,old_backend_rejected:true,dirty_guard:true,filtered_next_jump_and_approve:true,unknown_and_missing:true,url_reload:true,errors};
}
