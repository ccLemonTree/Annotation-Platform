async page=>{
  await page.bringToFront();await page.unrouteAll();page.setDefaultTimeout(10000);await page.setViewportSize({width:1440,height:1000});
  const origin='http://127.0.0.1:3000',checks=[],errors=[];
  const source=await(await page.request.get(origin+'/src/pages/TrainingPage.tsx')).text();
  const react=source.match(/["'](\/node_modules\/\.vite\/deps\/react\.js\?[^"']+)["']/)[1];
  const router=source.match(/["'](\/node_modules\/\.vite\/deps\/react-router-dom\.js\?[^"']+)["']/)[1];
  await page.addInitScript(()=>{if(location.origin==='http://127.0.0.1:3000')localStorage.setItem('user',JSON.stringify({id:1,username:'fixture',role:'admin',tenant_id:1}));});
  await page.route('**/src/App.tsx*',route=>route.fulfill({contentType:'application/javascript',body:`import React from '${react}';import{BrowserRouter}from'${router}';import Provider from '/src/ui/PlatformProvider.tsx';import Training from '/src/pages/TrainingPage.tsx';export default function App(){return React.createElement(BrowserRouter,null,React.createElement(Provider,null,React.createElement(Training)));}`}));
  const phases=['precheck','stage','archive','upload','unpack','container'];
  const run={id:'11111111-1111-4111-8111-111111111111',name:'后台启动隔离测试',kind:'rfdetr_medium',status:'draft',
    config:{gpu_uuids:['GPU-fixture'],parameters:{},output_subdir:'result',dataset_path:'',kind:'rfdetr_medium'},source:{kind:'snapshot',id:'snapshot',labels:['bike']},
    server:{id:'default',name:'隔离服务器'},output_dir:'/fixture',created_at:new Date().toISOString(),inspection:{ok:true,checks:[]}};
  let starts=0,reads=0;
  const pageError=error=>errors.push(error.message);page.on('pageerror',pageError);
  await page.route(origin+'/api/**',route=>{
    const request=route.request(),path=new URL(request.url()).pathname;
    if(path==='/api/training/catalog')return route.fulfill({json:{rfdetr_medium:{name:'RF-DETR Medium',defaults:{},schema:{properties:{}},environment:{}}}});
    if(path==='/api/training/servers')return route.fulfill({json:[]});
    if(path==='/api/projects')return route.fulfill({json:{items:[],total:0}});
    if(path==='/api/training/runs')return route.fulfill({json:{items:[run],total:1}});
    if(path==='/api/training/runs/'+run.id){reads++;return route.fulfill({json:run});}
    if(path.endsWith('/start')){
      starts++;run.status='starting';run.inspection.startup={state:'queued',phase:'queued',steps:phases,events:[{phase:'queued',message:'启动已提交，等待后台准备',time:new Date().toISOString()}]};
      return route.fulfill({status:202,json:run});
    }
    throw Error('Unexpected fixture request '+request.method()+' '+path);
  });
  try{
    await page.goto(origin+'/training');await page.getByRole('button',{name:run.name,exact:true}).click();
    await page.getByRole('button',{name:'启动训练',exact:true}).click();await page.getByRole('button',{name:'确认启动',exact:true}).click();
    await page.getByRole('dialog',{name:'启动此训练任务？'}).waitFor({state:'hidden'});
    await page.getByText('后台已接收启动任务，正在排队',{exact:true}).waitFor();
    if(starts!==1)throw Error('start was duplicated');checks.push('202 acknowledgement closes confirmation and shows the persistent startup tab');
    run.inspection.startup={...run.inspection.startup,state:'running',phase:'upload',archive_bytes:1024**3,events:[...run.inspection.startup.events,{phase:'upload',message:'正在传输冻结包（1024.0 MiB）',time:new Date().toISOString()}]};
    await page.getByText('正在传输冻结包（1024.0 MiB）',{exact:false}).waitFor();checks.push('detail polls saved background stage and package size');
    await page.getByRole('dialog').getByRole('button',{name:/关闭|Close/}).click();
    await page.getByRole('button',{name:run.name,exact:true}).click();await page.getByRole('tab',{name:'启动进度',exact:true}).click();
    await page.getByText('正在传输冻结包（1024.0 MiB）',{exact:false}).waitFor();checks.push('closing and reopening details retains preparation progress');
    await page.reload();await page.getByRole('button',{name:run.name,exact:true}).click();await page.getByRole('tab',{name:'启动进度',exact:true}).click();
    await page.getByText('正在传输冻结包（1024.0 MiB）',{exact:false}).waitFor();checks.push('page reload restores the saved stage without a new start request');
    run.status='unknown';run.error='SSH连接中断，请核对容器';run.inspection.startup={...run.inspection.startup,state:'failed'};
    await page.getByText('启动准备失败',{exact:true}).waitFor();await page.getByText(run.error,{exact:true}).waitFor();
    if(starts!==1||reads<3)throw Error('polling replayed a start');checks.push('background errors appear without resubmission');
    await page.screenshot({path:'output/playwright/training-startup.png'});
    if(errors.length)throw Error(errors.join('; '));return{checks,start_requests:starts,business_writes:0};
  }catch(error){throw Error(String(error)+' '+JSON.stringify({starts,reads,hidden:await page.evaluate(()=>document.hidden),dom:(await page.locator('body').innerText()).slice(-3500)}));}
  finally{await page.goto('about:blank');await page.unrouteAll();page.off('pageerror',pageError);}
}
