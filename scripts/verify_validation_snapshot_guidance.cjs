async page => {
  page.setDefaultTimeout(8000);
  const origin='http://127.0.0.1:3000', checks=[], writes=[], unexpected=[], errors=[];
  const compiled=await (await page.request.get(origin+'/src/components/validation/ValidationForms.tsx')).text();
  const react=compiled.match(/["'](\/node_modules\/\.vite\/deps\/react\.js\?[^"']+)["']/)[1];
  const snapshot=(id,split)=>({id,format:'yolo',created_at:'2026-11-06T00:00:00Z',manifest:{labels:['car'],assets:[{split}]}});
  let snapshots=[snapshot('val-package','val'),snapshot('test-package','test')],fail=false,delay=0;
  const app=`import React from '${react}';import {BrowserRouter} from '${react.replace('react.js','react-router-dom.js')}';import Provider from '/src/ui/PlatformProvider.tsx';import {CreateValidationSet} from '/src/components/validation/ValidationForms.tsx';import ModelPreviewPage from '/src/pages/ModelPreviewPage.tsx';export default function App(){return React.createElement(BrowserRouter,null,React.createElement(Provider,null,location.search.includes('preview')?React.createElement(ModelPreviewPage,{embeddedProject:{id:'fixture',project_name:'验证项目',task_type:'detection'}}):React.createElement(CreateValidationSet,{projects:[{id:'fixture',project_name:'验证项目'},{id:'other',project_name:'另一项目'}],projectId:'fixture',projectLocked:!location.search.includes('unlocked'),onClose:()=>{},onCreated:()=>{}})));}`;
  const check=(ok,name)=>{if(!ok)throw Error(name);checks.push(name);};
  await page.route('**/src/App.tsx*',route=>route.fulfill({contentType:'application/javascript',body:app}));
  await page.route('**/api/**',async route=>{const req=route.request(),path=new URL(req.url()).pathname;if(!path.startsWith('/api/'))return route.continue();
    if(path.endsWith('/snapshots')){const data=snapshots,error=fail,ms=delay;if(ms)await new Promise(r=>setTimeout(r,ms));return route.fulfill({status:error?503:200,json:error?{detail:'隔离读取失败'}:data}).catch(()=>{});}
    if(path==='/api/validation/sets'&&req.method()==='POST'){writes.push(req.postDataJSON());return route.fulfill({status:201,json:{id:'created'}});}
    if(path==='/api/auth/me')return route.fulfill({json:{id:1,username:'fixture',role:'admin'}});
    if(path==='/api/model-services')return route.fulfill({json:[]});
    if(path==='/api/model-previews/config')return route.fulfill({json:{max_images:100,max_upload_size:104857600,max_expanded_size:104857600,max_image_size:10485760,max_video_size:104857600}});
    if(path==='/api/model-previews')return route.fulfill({json:{items:[],total:0,page:1,page_size:20}});
    unexpected.push(path);return route.fulfill({status:599,json:{detail:'unmocked'}});
  });
  page.on('pageerror',e=>errors.push(e.message));
  async function open(){await page.goto(origin);await page.getByRole('dialog',{name:'创建验证集',exact:true}).waitFor();}
  async function pick(label,value){await page.getByRole('combobox',{name:label,exact:true}).click();await page.locator('.ant-select-dropdown:visible').getByText(value,{exact:true}).click();await page.locator('.ant-select-dropdown:visible').waitFor({state:'hidden'});}
  try{
    await open();await page.getByRole('combobox',{name:'冻结划分',exact:true}).waitFor({timeout:2000});
    check((await page.getByRole('dialog').innerText()).includes('冻结包 val/test 划分'),'项目内默认使用冻结val/test，无默默fallback');
    await page.getByLabel('验证集名称').fill('独立验证');await pick('冻结包','val-package · yolo · 2026-11-06T00:00:00Z');
    await pick('冻结划分','test（测试）');check(!(await page.getByRole('combobox',{name:'冻结包',exact:true}).locator('..').innerText()).includes('val-package'),'切换划分清空旧冻结包');
    await pick('冻结包','test-package · yolo · 2026-11-06T00:00:00Z');await page.getByRole('button',{name:'保存验证集',exact:true}).click();await page.waitForTimeout(150);check(writes.at(-1)?.split==='test'&&writes.at(-1)?.snapshot_id==='test-package','提交实际选择的冻结test版本');
    snapshots=[snapshot('test-only','test')];await open();await page.getByText('test（测试）',{exact:true}).waitFor();check(true,'仅有test时自动优先test而非空val');
    snapshots=[];await open();await page.getByRole('link',{name:'前往本项目导出准备',exact:true}).waitFor();check((await page.getByRole('link',{name:'前往本项目导出准备'}).getAttribute('href'))==='/projects/fixture/deliver','无冻结包时指向当前项目导出准备');check(await page.getByRole('button',{name:'保存验证集',exact:true}).isDisabled(),'无可用冻结划分时禁止提交');
    await pick('真值来源','当前已确认标注的样本');await page.getByText('当前样本未验证独立于训练数据',{exact:true}).waitFor();check(true,'允许显式选择当前样本并保留独立性提醒');
    await pick('真值来源','冻结包 val/test 划分');check(!(await page.getByRole('combobox',{name:'冻结包',exact:true}).locator('..').innerText()).includes('test-only'),'切换来源不残留冻结包');
    for(const width of [1440,390,320]){await page.setViewportSize({width,height:900});await page.screenshot({path:`output/playwright/validation-snapshot-empty-${width}.png`,fullPage:true});check(await page.evaluate(()=>document.documentElement.scrollWidth===document.documentElement.clientWidth),`${width}px无整页横向溢出`);}
    await page.getByRole('link',{name:'前往本项目导出准备',exact:true}).scrollIntoViewIfNeeded();await page.screenshot({path:'output/playwright/validation-snapshot-guidance-320.png',fullPage:true});check(await page.getByRole('link',{name:'前往本项目导出准备',exact:true}).isVisible(),'320px可滚动抵达导出准备链接');
    fail=true;await open();await page.getByRole('button',{name:'重试读取冻结包',exact:true}).waitFor();check(await page.getByRole('button',{name:'保存验证集',exact:true}).isDisabled(),'503保留推荐来源且阻止保存');
    fail=false;snapshots=[snapshot('recovered','val')];await page.getByRole('button',{name:'重试读取冻结包',exact:true}).click();await pick('冻结包','recovered · yolo · 2026-11-06T00:00:00Z');check(await page.getByRole('button',{name:'保存验证集',exact:true}).isEnabled(),'503后可重试并选择恢复的版本');
    delay=800;await open();check(await page.getByRole('combobox',{name:'冻结划分',exact:true}).isDisabled(),'冻结包读取期间禁止切换划分，防止响应覆盖用户选择');await page.waitForFunction(()=>!document.querySelector('[aria-label="冻结划分"]').disabled);check(await page.getByRole('combobox',{name:'冻结划分',exact:true}).isEnabled(),'读取完成后恢复划分选择');
    delay=600;await open();await pick('真值来源','当前已确认标注的样本');await page.waitForTimeout(700);check(await page.getByText('当前样本未验证独立于训练数据',{exact:true}).isVisible(),'切换来源后忽略迟到冻结包响应');
    delay=0;await page.goto(origin+'/?unlocked');await pick('真值来源','冻结包 val/test 划分');await pick('冻结包','recovered · yolo · 2026-11-06T00:00:00Z');snapshots=[snapshot('other-package','test')];await pick('验证项目','另一项目');await page.getByText('test（测试）',{exact:true}).waitFor();check(!(await page.getByRole('combobox',{name:'冻结包',exact:true}).locator('..').innerText()).includes('recovered'),'切换项目清空旧包并重新选择可用划分');await pick('冻结包','other-package · yolo · 2026-11-06T00:00:00Z');
    await page.goto(origin+'/?preview');await page.getByText('越高越严格，显示的框越少；不确定时保留默认值。',{exact:true}).waitFor();check(await page.getByText('去除重叠框的阈值；越低越容易去重，通常保留默认值。',{exact:true}).isVisible(),'快速测试阈值旁显示简短含义与默认值建议');
    for(const width of [1440,390,320]){await page.setViewportSize({width,height:900});await page.screenshot({path:`output/playwright/preview-threshold-guidance-${width}.png`,fullPage:true});check(await page.evaluate(()=>document.documentElement.scrollWidth===document.documentElement.clientWidth),`${width}px快速测试说明无横向溢出`);}
    check(unexpected.length===0,'所有API均被隔离夹具接管');check(errors.length===0,'无浏览器运行时错误');return{checks,writes,unexpected,errors,scope:'mock only, no business connections'};
  }catch(error){throw Error(error.message+' BODY '+await page.locator('body').innerText()+' ERRORS '+errors.join(';'));}finally{await page.goto('about:blank');await page.unrouteAll({behavior:'ignoreErrors'});}
}
