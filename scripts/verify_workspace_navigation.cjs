async(page)=>{
  page.setDefaultTimeout(8000);
  const projectId='33333333-3333-4333-8333-333333333333';
  const assets=Array.from({length:4676},(_,i)=>({id:'aaaaaaaa-0000-4000-8000-'+String(i).padStart(12,'0'),
    project_id:projectId,image_id:null,original_name:`navigation-${i}.png`,blob_path:'fixture.png',content_hash:String(i),
    width:640,height:480,format:'PNG',file_size:100,source_type:'upload',annotation_status:'pending'}));
  const project={id:projectId,project_name:'切图测试',task_type:'detection',label_schema:{labels:['fire']},total_assets:4676,annotated_count:1,reviewed_count:0};
  const records=new Map(),checks=[];let listCalls=0,neighborCalls=0,ordinalCalls=0,writes=0,saveFailure=0,navigationFailure=false,assetFailure=0,jumpFailure=0,withHistory=false;
  records.set(assets[2100].id,{id:'bbbbbbbb-0000-4000-8000-000000000001',asset_id:assets[2100].id,
    annotation_data:{boxes:[{label:'fire',x:50,y:50,width:80,height:80}]},version:1,is_latest:true,review_status:'pending'});
  const pattern='http://localhost:3000/api/**';
  const originalUser=await page.evaluate(()=>localStorage.getItem('user'));
  await page.evaluate(()=>localStorage.setItem('user',JSON.stringify({id:910001,tenant_id:1,username:'navigation-fixture',role:'admin'})));
  const dialogs = dialog => dialog.accept();
  page.on('dialog',dialogs);
  await page.route(pattern,async route=>{
    const request=route.request(),url=new URL(request.url()),path=url.pathname;
    if(path.startsWith('/api/analytics/'))return route.fulfill({json:{}});
    if(path==='/api/model-services')return route.fulfill({json:[]});
    const reviewStatus=url.searchParams.get('review_status');
    const queue=reviewStatus?assets.filter(item=>records.get(item.id)?.review_status===reviewStatus):assets;
    const headers=reviewStatus?{'X-Annotation-Review-Filter':'1'}:{};
    if(path==='/api/projects/'+projectId)return route.fulfill({json:project});
    if(path==='/api/projects/'+projectId+'/stats')return route.fulfill({json:project});
    if(path==='/api/projects')return route.fulfill({json:{items:[project],total:1,page:1,page_size:10}});
    if(path==='/api/assets'){
      if(url.searchParams.get('page_size')==='1'){
        ordinalCalls++;
        if(jumpFailure)return route.fulfill({status:jumpFailure,json:{detail:'ordinal fixture interrupted'}});
        const index=Number(url.searchParams.get('page'))-1;
        return route.fulfill({headers,json:{items:queue.slice(index,index+1),total:queue.length,page:index+1,page_size:1}});
      }
      listCalls++;return route.fulfill({headers,json:{items:queue.slice(0,100),total:queue.length,page:Number(url.searchParams.get('page')||1),page_size:100}});
    }
    if(path.startsWith('/api/assets/')){
      const id=path.split('/')[3],index=assets.findIndex(item=>item.id===id);
      if(index<0)throw Error('Unexpected fixture asset');
      if(assetFailure&&!path.endsWith('/neighbors')&&!path.endsWith('/content')){
        const status=assetFailure;assetFailure=0;return route.fulfill({status,json:{detail:'asset fixture interrupted'}});
      }
      if(path.endsWith('/neighbors')){
        neighborCalls++;
        if(navigationFailure)return route.fulfill({status:503,json:{detail:'navigation fixture interrupted'}});
        const position=queue.indexOf(assets[index]);
        return route.fulfill({headers,json:{position:position+1,total:queue.length,previous_id:queue[position-1]?.id||null,next_id:queue[position+1]?.id||null,...(reviewStatus?{matched:position>=0}:{})}});
      }
      if(path.endsWith('/content'))return route.fulfill({contentType:'image/png',body:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/l9sAAAAASUVORK5CYII=','base64')});
      return route.fulfill({json:assets[index]});
    }
    if(path.startsWith('/api/annotations')){
      if(path.endsWith('/events'))return route.fulfill({json:[]});
      if(request.method()==='GET'){
        const record=records.get(url.searchParams.get('asset_id'));
        const items=record?[record]:[];
        if(withHistory&&record)items.push({...record,id:'bbbbbbbb-0000-4000-8000-000000009999',version:1,is_latest:false});
        return route.fulfill({json:{items,total:items.length,page:1,page_size:100}});
      }
      writes++;
      if(saveFailure)return route.fulfill({status:saveFailure,json:{detail:'save fixture interrupted'}});
      const body=request.postDataJSON(),old=[...records.values()].find(item=>path.endsWith('/'+item.id));
      const id=old?.asset_id||body.asset_id;
      const record={id:old?.id||'bbbbbbbb-0000-4000-8000-'+String(writes).padStart(12,'0'),asset_id:id,
        annotation_data:body.annotation_data,version:(old?.version||0)+1,is_latest:true,review_status:'pending'};
      records.set(id,record);return route.fulfill({json:record});
    }
    if(request.method()!=='GET')throw Error('Unexpected fixture write '+path);
    throw Error('Unexpected fixture read '+path);
  });
  const canvas=page.getByRole('img',{name:'检测画框区域'});
  const position=async index=>{
    await page.waitForFunction(expected => document.querySelector('input[aria-label="跳转图片序号"]')?.value === String(expected),index+1);
    await page.waitForURL(url=>url.pathname==='/annotate/'+assets[index].id);await canvas.waitFor();
    await page.waitForFunction(() => [...document.querySelectorAll('button')].some(button => button.textContent.trim()==='重新加载'&&!button.disabled));
  };
  const edit=async width=>{
    await page.getByRole('button',{name:'1 · fire',exact:true}).click();
    await page.getByRole('spinbutton',{name:'框width',exact:true}).fill(String(width));await page.keyboard.press('Tab');
  };
  try{
    await page.goto('http://localhost:3000/annotate/'+assets[2100].id);await canvas.waitFor();await position(2100);
    if(listCalls!==0||neighborCalls!==1)throw Error('Navigation scanned project list instead of locating current image');
    checks.push('4676-image project locates current image with one neighbor request and no list scan');
    await page.getByRole('button',{name:'下一张',exact:true}).click();await position(2101);
    await page.getByRole('button',{name:'上一张',exact:true}).click();await position(2100);
    if(writes)throw Error('Unchanged navigation saved a version');
    checks.push('previous and next work without unnecessary saves');
    await edit(90);await page.getByRole('button',{name:'下一张',exact:true}).click();await position(2101);
    if(writes!==1||records.get(assets[2100].id).annotation_data.boxes[0].width!==90)throw Error('Auto-save was missing or duplicated');
    checks.push('edited box saves exactly once before next image');
    await page.getByRole('button',{name:'上一张',exact:true}).click();await position(2100);await edit(100);
    for(const status of [500,409]){
      saveFailure=status;await page.getByRole('button',{name:'下一张',exact:true}).click();
      await page.getByText('保存失败；若版本冲突，请先记录当前修改，再重新加载最新版本',{exact:true}).waitFor();await position(2100);
      if(await page.getByRole('spinbutton',{name:'框width',exact:true}).inputValue()!=='100')throw Error('Save failure lost draft');
      checks.push(status+' preserves current image and draft');
    }
    saveFailure=0;await page.getByRole('button',{name:'下一张',exact:true}).click();await position(2101);
    await page.getByRole('checkbox',{name:'此图没有项目中的目标（负样本）'}).check();await canvas.focus();await page.keyboard.press('d');await position(2102);
    if(records.get(assets[2101].id)?.annotation_data.boxes.length!==0)throw Error('Negative draft not saved');
    await canvas.focus();await page.keyboard.press('ArrowLeft');await position(2101);
    checks.push('keyboard navigation saves confirmed negative and moves back');
    for(const index of [0,1])records.set(assets[index].id,{id:'review-entry-'+index,asset_id:assets[index].id,annotation_data:{boxes:[]},version:1,is_latest:true,review_status:'pending'});
    await page.goto('http://localhost:3000/annotate/'+assets[0].id+'?from=review&project_id='+projectId+'&review_status=pending');await position(0);
    if(!await page.getByRole('button',{name:'上一张',exact:true}).isDisabled())throw Error('First previous is enabled');
    await page.getByRole('button',{name:'下一张',exact:true}).click();await position(1);
    if(new URL(page.url()).searchParams.get('review_status')!=='pending')throw Error('Review return context lost');
    await page.goto('http://localhost:3000/annotate/'+assets.at(-1).id);await position(4675);
    if(!await page.getByRole('button',{name:'下一张',exact:true}).isDisabled())throw Error('Last next is enabled');
    checks.push('first/last boundaries and review return context preserved');
    navigationFailure=true;await page.goto('http://localhost:3000/annotate/'+assets[2100].id);await canvas.waitFor();
    await page.getByRole('button',{name:'重试切图读取',exact:true}).waitFor();
    if(!await page.getByRole('button',{name:'下一张',exact:true}).isDisabled())throw Error('Stale navigation enabled after read failure');
    navigationFailure=false;await page.getByRole('button',{name:'重试切图读取',exact:true}).click();await position(2100);
    checks.push('neighbor failure keeps image visible and retry restores navigation');
    await canvas.focus();await page.keyboard.press('n');
    if(!await page.getByRole('checkbox',{name:'此图没有项目中的目标（负样本）'}).isChecked())throw Error('N did not mark negative');
    if(await page.getByRole('button',{name:'1 · fire',exact:true}).count())throw Error('N did not clear boxes');
    await page.keyboard.press('Control+z');
    await page.getByRole('button',{name:'1 · fire',exact:true}).waitFor();
    if(await page.getByRole('checkbox',{name:'此图没有项目中的目标（负样本）'}).isChecked())throw Error('Undo did not restore positive');
    checks.push('N clears boxes and confirms negative in one undoable change');
    const jump=page.getByRole('spinbutton',{name:'跳转图片序号',exact:true});
    await jump.fill('3200');await jump.press('n');
    if(await page.getByRole('checkbox',{name:'此图没有项目中的目标（负样本）'}).isChecked())throw Error('N interrupted input');
    await jump.fill('3200');await jump.press('Enter');await position(3199);
    checks.push('jump input ignores workbench shortcuts and Enter navigates to requested ordinal');
    await canvas.focus();await page.keyboard.press('n');
    const writesBeforeJump=writes;
    await jump.fill('2101');await jump.press('Enter');await position(2100);
    if(writes!==writesBeforeJump+1||records.get(assets[3199].id)?.annotation_data.boxes.length!==0)throw Error('Ordinal jump did not save negative once');
    checks.push('ordinal jump auto-saves negative before changing image');
    await edit(120);saveFailure=409;
    await jump.fill('3200');await jump.press('Enter');
    await page.getByText('保存失败；若版本冲突，请先记录当前修改，再重新加载最新版本',{exact:true}).waitFor();await position(2100);
    if(await page.getByRole('spinbutton',{name:'框width',exact:true}).inputValue()!=='120')throw Error('Jump conflict lost draft');
    saveFailure=0;await jump.fill('3200');await jump.press('Enter');await position(3199);
    checks.push('ordinal jump conflict keeps image and draft; retry succeeds');
    for(const path of ['/projects/'+projectId+'/images','/annotations?project_id='+projectId,'/annotations','/projects']){
      await page.goto('http://localhost:3000'+path);
      await page.getByRole('button',{name:'继续标注',exact:true}).click();await position(3199);
    }
    await page.reload();await position(3199);
    checks.push('all four project entries resume last image and browser refresh keeps position');
    const ordinalBeforeInvalid=ordinalCalls;
    await jump.fill('');await jump.press('Enter');
    await page.getByText('请输入1到4676之间的整数',{exact:true}).waitFor();
    if(ordinalCalls!==ordinalBeforeInvalid)throw Error('Empty ordinal sent lookup');
    await jump.fill('2101');jumpFailure=503;await jump.press('Enter');
    await page.getByText('跳转失败，当前图片与草稿已保留，请重试',{exact:true}).waitFor();await position(3199);
    jumpFailure=0;await jump.fill('2101');await jump.press('Enter');await position(2100);
    checks.push('invalid ordinal makes no request; lookup failure keeps image and allows retry');
    await page.getByRole('button',{name:'快捷键',exact:true}).click();await page.keyboard.press('n');
    if(await page.getByRole('checkbox',{name:'此图没有项目中的目标（负样本）'}).isChecked())throw Error('N interrupted shortcut dialog');
    await page.keyboard.press('Escape');
    await page.getByRole('dialog',{name:'标注快捷键'}).waitFor({state:'hidden'});
    checks.push('negative shortcut is ignored while shortcut dialog is open');
    await page.goto('http://localhost:3000/projects/'+projectId+'/images');
    assetFailure=500;await page.getByRole('button',{name:'继续标注',exact:true}).click();
    await page.getByText('继续位置读取失败，请重试',{exact:true}).waitFor();
    assetFailure=0;await page.getByRole('button',{name:'继续标注',exact:true}).click();await position(2100);
    checks.push('resume network failure retains bookmark for retry');
    await page.goto('http://localhost:3000/projects/'+projectId+'/images');
    assetFailure=404;await page.getByRole('button',{name:'继续标注',exact:true}).click();
    await position(0);
    assetFailure=0;await page.goto('http://localhost:3000/annotate/'+assets[2100].id);await position(2100);
    checks.push('deleted bookmarked asset falls back to first eligible image');
    withHistory=true;await page.reload();await position(2100);
    await page.getByRole('combobox',{name:'标注历史版本',exact:true}).click();
    await page.getByText('v1 · 待审核',{exact:true}).click();
    await page.getByRole('button',{name:'返回最新版本',exact:true}).waitFor();
    await canvas.focus();await page.keyboard.press('n');
    if(await page.getByRole('checkbox',{name:'此图没有项目中的目标（负样本）'}).isChecked())throw Error('N changed historical version');
    if(!await page.getByRole('button',{name:'标记负样本（N）',exact:true}).isDisabled())throw Error('Negative action enabled on historical version');
    await page.getByRole('button',{name:'返回最新版本',exact:true}).click();
    checks.push('historical annotation rejects N and disables negative action');
    await page.screenshot({path:'output/playwright/workspace-navigation.png'});
    const result={checks,listCalls,neighborCalls,ordinalCalls,writesIntercepted:writes};
    console.log(JSON.stringify(result));return result;
  }catch(error){
    throw Error(JSON.stringify({checks,failure:String(error),url:page.url(),buttons:await page.getByRole('button').allTextContents()}));
  }finally{
    await page.evaluate(project=>Object.keys(localStorage).filter(key=>key.startsWith('annotation-progress:')&&key.endsWith(':'+project)).forEach(key=>localStorage.removeItem(key)),projectId);
    await page.evaluate(value=>{if(value===null)localStorage.removeItem('user');else localStorage.setItem('user',value);},originalUser);
    await page.unroute(pattern);
    const undo=page.getByRole('button',{name:'撤销标注操作',exact:true});
    for(let i=0;i<100&&await undo.count()&&await undo.isEnabled();i++)await undo.click();
    await page.goto('http://localhost:3000/assets');
    page.off('dialog',dialogs);
    page.setDefaultTimeout(30000);
  }
}
