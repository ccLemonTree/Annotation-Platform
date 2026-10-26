async (page) => {
  page.setDefaultTimeout(12000);
  const checks = [];
  let blockedWrites = 0, mode = 'ok', calls = 0, release, held;
  const guard = async route => {
    if (!['GET', 'HEAD', 'OPTIONS'].includes(route.request().method())) {
      blockedWrites++; return route.abort();
    }
    return route.continue();
  };
  const fixture = {server_name:'GPU页面验收',collected_at:'2026-10-15T08:00:00+00:00',cached:false,
    processes_complete:true,warnings:[],gpus:[
      {index:0,uuid:'GPU-fixture-0',name:'A100-fixture',driver_version:'615',memory_total_mib:40960,memory_used_mib:37575,memory_free_mib:2897,
       utilization_percent:0,temperature_c:33,power_draw_w:38,power_limit_w:250,mig_mode:'Disabled'},
      {index:1,uuid:'GPU-fixture-1',name:'A100-fixture',driver_version:'615',memory_total_mib:null,memory_used_mib:0,memory_free_mib:null,
       utilization_percent:null,temperature_c:null,power_draw_w:null,power_limit_w:250,mig_mode:'Enabled'},
    ],processes:[{gpu_uuid:'GPU-fixture-0',pid:123,name:'fixture-model',memory_used_mib:37000},
      {gpu_uuid:'GPU-fixture-1',pid:123,name:'fixture-model',memory_used_mib:null}]};
  const mock = async route => {
    calls++;
    if (mode === 'hold') { held = new Promise(resolve => {release=resolve;}); await held; }
    try {
      if (mode === 'error') return await route.fulfill({status:503,json:{detail:'测试连接不可用'}});
      if (mode === 'forbidden') return await route.fulfill({status:403,json:{detail:'无权限'}});
      return await route.fulfill({json:mode === 'partial' ? {...fixture,processes_complete:false,processes:[],warnings:['进程采集失败']} : fixture});
    } catch (error) { if (!/closed|handled|invalid|intercept|cancel/i.test(error.message)) throw error; }
  };
  let originalUser;
  await page.route('**/api/**', guard);
  try {
    await page.setViewportSize({width:1440,height:900});
    const live = page.waitForResponse(response => response.url().endsWith('/api/gpu-resources'));
    await page.goto('http://localhost:3000/gpu-resources');
    const response = await live, snapshot = await response.json();
    if(response.status() !== 200 || snapshot.gpus.length !== 4) throw Error('Live GPU API did not return four cards');
    await page.getByText('资源概览',{exact:true}).waitFor();
    for(const gpu of snapshot.gpus) await page.getByRole('cell').filter({hasText:gpu.uuid}).first().waitFor();
    await page.getByText('VLLM::Worker_TP0',{exact:true}).waitFor();
    await page.getByRole('switch',{name:'自动30秒刷新'}).uncheck();
    await page.screenshot({path:'output/playwright/gpu-resources-live.png',fullPage:true});
    checks.push('real page and API display four A100s, UUIDs and vLLM processes');
    await page.setViewportSize({width:390,height:844});
    if(await page.locator('html').evaluate(el=>el.scrollWidth>el.clientWidth+1))throw Error('Page horizontally overflows');
    await page.screenshot({path:'output/playwright/gpu-resources-narrow.png',fullPage:true});
    checks.push('narrow page scrolls tables internally');
    await page.setViewportSize({width:1366,height:768});
    originalUser=await page.evaluate(()=>localStorage.getItem('user'));
    await page.route('**/api/gpu-resources', mock);
    await page.clock.install();
    mode='error'; await page.reload();
    await page.getByText('GPU资源读取失败',{exact:true}).waitFor();
    if(await page.getByText('资源概览',{exact:true}).count())throw Error('Initial failure fabricated GPU data');
    checks.push('initial connection failure shows retry without zero-valued inventory');
    mode='ok'; await page.getByRole('button',{name:/^重\s*试$/}).click();
    await page.getByText('GPU页面验收',{exact:true}).waitFor();
    if(await page.locator('.ant-statistic').filter({hasText:'总显存'}).locator('.ant-statistic-content').innerText() !== '--MiB') {
      const text=await page.locator('.ant-statistic').filter({hasText:'总显存'}).innerText();
      if(!text.includes('--'))throw Error('Unknown memory summed as zero');
    }
    await page.getByText('GPU-fixture-1',{exact:true}).first().waitFor();
    checks.push('retry, unknown metrics and cross-card PID mappings render');
    mode='error'; await page.getByRole('button',{name:'手动刷新',exact:true}).click();
    await page.getByText('刷新失败：上次数据已陈旧',{exact:true}).waitFor();
    await page.getByText('GPU页面验收',{exact:true}).waitFor();
    checks.push('refresh failure retains visibly stale snapshot');
    mode='partial'; await page.getByRole('button',{name:'手动刷新',exact:true}).click();
    await page.getByText('计算进程未采集或采集不完整',{exact:true}).waitFor();
    if(await page.getByText('无计算进程（本次采集）',{exact:true}).count())throw Error('Missing process data shown as empty');
    checks.push('process collection failure differs from empty process list');
    mode='hold'; const previous=calls;
    await page.getByRole('button',{name:'手动刷新',exact:true}).click();
    await page.waitForFunction(()=>document.querySelector('.ant-btn-loading')!==null);
    await page.clock.runFor(60001);
    if(calls!==previous+1)throw Error('Overlapping refresh requests');
    mode='ok'; release(); await page.getByRole('button',{name:'手动刷新',exact:true}).waitFor();
    await page.waitForFunction(()=>document.querySelector('.ant-btn-loading')===null);
    checks.push('slow collector does not overlap automatic refresh');
    await page.evaluate(()=>{
      window.__gpuHidden=true;
      Object.defineProperty(document,'hidden',{configurable:true,get:()=>window.__gpuHidden});
      document.dispatchEvent(new Event('visibilitychange'));
    });
    const paused=calls; await page.clock.runFor(60001);
    if(calls!==paused)throw Error('Hidden page kept polling');
    await page.evaluate(()=>{window.__gpuHidden=false;document.dispatchEvent(new Event('visibilitychange'));});
    await page.waitForFunction(()=>document.querySelector('.ant-btn-loading')===null);
    checks.push('hidden page pauses automatic sampling');
    mode='forbidden'; await page.getByRole('button',{name:'手动刷新',exact:true}).click();
    await page.getByText('无权限',{exact:true}).waitFor();
    const denied=calls; await page.clock.runFor(60001);
    if(calls!==denied)throw Error('403 page kept collecting');
    checks.push('backend role rejection stops polling and hides data');
    await page.clock.resume();
    await page.evaluate(()=>{const user=JSON.parse(localStorage.getItem('user'));user.role='annotator';localStorage.setItem('user',JSON.stringify(user));});
    const normalBefore=calls; await page.reload();
    await page.getByText('无权限',{exact:true}).waitFor();
    if(calls!==normalBefore)throw Error('Non-admin sent GPU requests');
    if(await page.getByRole('menuitem',{name:'GPU资源',exact:true}).count())throw Error('Non-admin menu exposed');
    checks.push('ordinary user direct route is denied without sampling or menu');
  } finally {
    if(release)release();
    await page.clock.resume().catch(()=>{});
    await page.unroute('**/api/gpu-resources',mock);
    if(originalUser)await page.evaluate(value=>localStorage.setItem('user',value),originalUser);
    await page.unroute('**/api/**',guard);
    await page.goto('http://localhost:3000/gpu-resources');
    await page.getByText('资源概览',{exact:true}).waitFor();
    await page.getByRole('switch',{name:'自动30秒刷新'}).uncheck();
  }
  if(blockedWrites)throw Error('Unexpected business writes attempted: '+blockedWrites);
  return {checks,fixtureRequests:calls,blockedWrites};
}
