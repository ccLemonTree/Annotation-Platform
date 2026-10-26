// Run with node scripts/verify_gpu_resources.cjs. Uses installed TypeScript; no server or real data.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');
const root = path.resolve(__dirname, '..');
const ts = require(path.join(root, 'frontend/node_modules/typescript'));

function compile(file, imports, globals = {}) {
  assert.ok(fs.existsSync(path.join(root, file)), `${file} must exist`);
  const source = fs.readFileSync(path.join(root, file), 'utf8');
  const code = ts.transpileModule(source, { compilerOptions: {
    module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022, esModuleInterop: false,
  } }).outputText;
  const exports = {};
  vm.runInNewContext(code, { exports, require: name => {
    assert.ok(name in imports, `Unexpected import: ${name}`);
    return imports[name];
  }, AbortController, Date, console, ...globals }, { filename: file });
  return exports;
}

// Exercise the real page's handlers/effects; AntD and React hooks are in-memory substitutes.
function mount(role = 'admin', initiallyHidden = false) {
  let cursor = 0, dirty = true, tree, mounted = true, hidden = initiallyHidden;
  const user = JSON.stringify({ role });
  const slots = [], effects = [], timers = new Map(), listeners = new Map(), calls = [];
  const equal = (a, b) => a && b && a.length === b.length && a.every((v, i) => Object.is(v, b[i]));
  const react = {
    useState(initial) {
      const id = cursor++;
      if (!(id in slots)) slots[id] = typeof initial === 'function' ? initial() : initial;
      return [slots[id], value => { slots[id] = typeof value === 'function' ? value(slots[id]) : value; dirty = true; }];
    },
    useRef(initial) { const id = cursor++; return slots[id] ||= { current: initial }; },
    useCallback(fn, deps) {
      const id = cursor++;
      if (!equal(slots[id]?.deps, deps)) slots[id] = { deps, fn };
      return slots[id].fn;
    },
    useEffect(fn, deps) {
      const id = cursor++;
      if (!equal(slots[id]?.deps, deps)) effects.push(() => {
        slots[id]?.cleanup?.(); slots[id] = { deps, setup: fn, cleanup: fn() };
      });
    },
  };
  const jsx = (type, props) => ({ type, props });
  const component = name => name;
  const antd = Object.fromEntries(['Alert', 'Button', 'Card', 'Descriptions', 'Empty', 'Progress', 'Result', 'Space', 'Spin', 'Switch', 'Table', 'Tag', 'Row', 'Col', 'Statistic'].map(name => [name, component(name)]));
  antd.Typography = { Text: 'Text', Paragraph: 'Paragraph', Title: 'Title' };
  const document = {
    get hidden() { return hidden; }, get visibilityState() { return hidden ? 'hidden' : 'visible'; },
    addEventListener: (event, fn) => listeners.set(event, fn),
    removeEventListener: event => listeners.delete(event),
  };
  const page = compile('frontend/src/components/gpu/GpuSnapshot.tsx', {
    react, 'react/jsx-runtime': { jsx, jsxs: jsx, Fragment: 'Fragment' }, antd,
    axios: { default: { isAxiosError: error => error?.isAxiosError, isCancel: error => error?.code === 'ERR_CANCELED' } },
    '../../api/gpuResources': { getGpuResources(signal) {
      let resolve, reject;
      const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
      calls.push({ signal, resolve, reject }); return promise;
    } },
  }, {
    document, localStorage: { getItem: () => user },
    setInterval(fn, delay) { assert.equal(delay, 30000); const id = {}; timers.set(id, fn); return id; },
    clearInterval: id => timers.delete(id),
  }).default;
  function render() {
    for (let i = 0; dirty && mounted; i++) {
      assert.ok(i < 20, 'render must settle'); dirty = false; cursor = 0; tree = page();
      effects.splice(0).forEach(fn => fn());
    }
  }
  async function flush() { for (let i = 0; i < 4; i++) { await Promise.resolve(); render(); } }
  function nodes(value) {
    if (!value || typeof value !== 'object') return [];
    if (Array.isArray(value)) return value.flatMap(nodes);
    return [value, ...Object.values(value.props || {}).flatMap(nodes)];
  }
  function text(value) {
    if (value == null || typeof value === 'boolean' || typeof value === 'function') return '';
    if (typeof value !== 'object') return String(value);
    return Object.values(value.props || value).map(text).join(' ');
  }
  render();
  return {
    calls, timers, nodes: () => nodes(tree), text: (value = tree) => text(value), flush,
    async clickRefresh() { nodes(tree).find(n => n.type === 'Button' && /刷新|重试/.test(text(n))).props.onClick(); await flush(); },
    async auto(value) { nodes(tree).find(n => n.type === 'Switch').props.onChange(value); await flush(); },
    async visibility(value) { hidden = value; listeners.get('visibilitychange')?.(); await flush(); },
    async tick() { [...timers.values()].forEach(fn => fn()); await flush(); },
    async replayEffects() {
      slots.filter(slot => slot?.setup).forEach(slot => slot.cleanup?.());
      slots.filter(slot => slot?.setup).forEach(slot => { slot.cleanup = slot.setup(); });
      await flush();
    },
    unmount() { mounted = false; slots.forEach(slot => slot?.cleanup?.()); },
  };
}

const fixture = () => ({
  server_name: 'gpu-fixture', collected_at: new Date().toISOString(), cached: false,
  processes_complete: true, warnings: [],
  gpus: [0, 1].map(index => ({ index, uuid: `GPU-full-uuid-${index}`, name: 'Test GPU', driver_version: '550.1',
    memory_total_mib: 16000, memory_used_mib: 1000, memory_free_mib: 15000,
    utilization_percent: 0, temperature_c: 42, power_draw_w: 20, power_limit_w: 70, mig_mode: 'Disabled' })),
  processes: [{ gpu_uuid: 'GPU-full-uuid-0', pid: 123, name: 'compute-fixture', memory_used_mib: 1000 }],
});
const httpError = (status, detail) => ({ isAxiosError: true, response: { status, data: { detail } } });

test('API uses shared authenticated client, 45s timeout and caller AbortSignal', async () => {
  let call;
  const data = fixture();
  const api = compile('frontend/src/api/gpuResources.ts', { './client': { default: {
    async get(...args) { call = args; return { data }; },
  } } });
  const controller = new AbortController();
  assert.equal(await api.getGpuResources(controller.signal), data);
  assert.equal(call[0], '/api/gpu-resources');
  assert.equal(call[1].timeout, 45000); assert.equal(call[1].signal, controller.signal);
  assert.equal(await api.getGpuResources(controller.signal, '11111111-1111-4111-8111-111111111111'), data);
  assert.equal(call[0], '/api/gpu-resources/servers/11111111-1111-4111-8111-111111111111/resources');
  assert.equal(call[1].timeout, 45000); assert.equal(call[1].signal, controller.signal);
});

test('ordinary user sees permission denial and never collects', async () => {
  const page = mount('user');
  assert.match(page.text(), /无权限/);
  await page.tick(); await page.visibility(true); await page.visibility(false);
  assert.equal(page.calls.length, 0); page.unmount();
});

test('default 30s polling never overlaps, pauses while hidden, allows manual refresh when off', async () => {
  const page = mount();
  assert.equal(page.calls.length, 1); assert.equal(page.timers.size, 1);
  await page.tick(); await page.clickRefresh(); assert.equal(page.calls.length, 1);
  page.calls[0].resolve(fixture()); await page.flush();
  await page.visibility(true); await page.tick(); assert.equal(page.calls.length, 1);
  await page.visibility(false); assert.equal(page.calls.length, 2);
  page.calls[1].resolve(fixture()); await page.flush();
  await page.auto(false); await page.tick(); assert.equal(page.calls.length, 2);
  await page.clickRefresh(); assert.equal(page.calls.length, 3);
  page.unmount(); assert.equal(page.calls[2].signal.aborted, true); assert.equal(page.timers.size, 0);
  page.calls[2].resolve({ ...fixture(), server_name: 'late-response' }); await page.flush();
  assert.doesNotMatch(page.text(), /late-response/);
});

test('failed refresh keeps last data and a stale warning through retry; success clears it', async () => {
  const page = mount(); page.calls[0].resolve(fixture()); await page.flush();
  await page.clickRefresh(); page.calls[1].reject(httpError(503, 'GPU服务器连接失败，请稍后重试')); await page.flush();
  assert.match(page.text(), /gpu-fixture/); assert.match(page.text(), /陈旧/); assert.match(page.text(), /GPU服务器连接失败/);
  await page.clickRefresh(); assert.match(page.text(), /陈旧/);
  page.calls[2].resolve({ ...fixture(), server_name: 'new-server' }); await page.flush();
  assert.match(page.text(), /new-server/); assert.doesNotMatch(page.text(), /陈旧/); page.unmount();
});

test('late response from a cancelled effect cannot overwrite a newer result or clear its loading state', async () => {
  const page = mount(); await page.replayEffects();
  assert.equal(page.calls[0].signal.aborted, true); assert.equal(page.calls.length, 2);
  page.calls[0].resolve({ ...fixture(), server_name: 'cancelled-server' }); await page.flush();
  assert.doesNotMatch(page.text(), /cancelled-server/);
  assert.equal(page.nodes().find(n => n.type === 'Button').props.loading, true);
  await page.clickRefresh(); assert.equal(page.calls.length, 2);
  page.calls[1].resolve({ ...fixture(), server_name: 'current-server' }); await page.flush();
  assert.match(page.text(), /current-server/); page.unmount();
});

test('initially hidden page collects only after it becomes visible', async () => {
  const page = mount('admin', true); assert.equal(page.calls.length, 0); assert.equal(page.timers.size, 0);
  await page.visibility(false); assert.equal(page.calls.length, 1);
  page.unmount(); page.calls[0].resolve(fixture()); await page.flush();
});

test('summary shows readable Shanghai time with original UTC on hover and concise monitoring copy', async () => {
  const page = mount();
  page.calls[0].resolve({ ...fixture(), collected_at: '2026-10-15T01:02:03Z' }); await page.flush();
  const time = page.nodes().find(n => n.type === 'Descriptions').props.items.find(item => item.key === 'time');
  assert.equal(time.label, '更新时间（北京时间）');
  assert.match(page.text(time.children), /2026\/10\/15 09:02:03/);
  assert.equal(time.children.props.title, '2026-10-15T01:02:03Z');
  assert.equal(page.nodes().find(n => n.type === 'Alert').props.description, '查看当前GPU状态及计算进程。低利用率仍可能有模型占用显存。');
  assert.doesNotMatch(page.text(), /任一卡显存未知|不将未知计为0/);
  page.unmount();
});

test('first error can retry; 504 detail is readable, client timeout has safe Chinese fallback', async () => {
  const page = mount(); page.calls[0].reject(httpError(504, 'GPU采集超时，请稍后重试')); await page.flush();
  assert.match(page.text(), /GPU采集超时/); await page.clickRefresh();
  page.calls[1].reject({ isAxiosError: true, code: 'ECONNABORTED' }); await page.flush();
  assert.match(page.text(), /超时/); await page.clickRefresh();
  page.calls[2].resolve(fixture()); await page.flush(); assert.match(page.text(), /gpu-fixture/); page.unmount();
});

test('unknown memory is not summed as zero; GPU UUID, process mapping, and partial collection remain visible', async () => {
  const page = mount(), data = fixture();
  data.gpus[1].memory_total_mib = null; data.gpus[1].memory_free_mib = null;
  data.processes_complete = false; data.cached = true; data.warnings = ['进程采集不完整'];
  page.calls[0].resolve(data); await page.flush();
  const summaries = page.nodes().filter(n => n.type === 'Statistic');
  assert.equal(summaries.find(n => n.props.title === '总显存').props.value, '--');
  assert.equal(summaries.find(n => n.props.title === '实际剩余显存').props.value, '--');
  const tables = page.nodes().filter(n => n.type === 'Table');
  for (const table of tables) {
    const cells = table.props.dataSource.flatMap(row => table.props.columns.map(col => col.render ? col.render(row[col.dataIndex], row) : row[col.dataIndex]));
    const cellText = page.text(cells);
    assert.match(cellText, /GPU-full-uuid-0/);
    if (table.props.dataSource[0]?.pid) { assert.match(cellText, /123/); assert.match(cellText, /compute-fixture/); }
  }
  assert.match(page.text(), /进程采集不完整/); assert.match(page.text(), /缓存/); page.unmount();
});

test('empty collected processes differ from uncollected processes and backend 403 stops polling', async () => {
  const page = mount(), data = fixture(); data.processes = [];
  page.calls[0].resolve(data); await page.flush(); assert.match(page.text(), /无计算进程/);
  await page.clickRefresh(); page.calls[1].resolve({ ...data, processes_complete: false }); await page.flush();
  assert.match(page.text(), /未采集|不完整/); assert.doesNotMatch(page.text(), /无计算进程/);
  await page.clickRefresh(); page.calls[2].reject(httpError(403, '无权限')); await page.flush();
  assert.match(page.text(), /无权限/); await page.tick(); assert.equal(page.calls.length, 3); page.unmount();
});

test('route and admin-only menu are connected with minimal existing-layout edits', () => {
  const app = fs.readFileSync(path.join(root, 'frontend/src/App.tsx'), 'utf8');
  assert.match(app, /path="gpu-resources"\s+element=\{<GpuResourcesPage\s*\/?>/);
  const layout = fs.readFileSync(path.join(root, 'frontend/src/layouts/MainLayout.tsx'), 'utf8');
  assert.match(layout, /key: '\/gpu-resources', label: 'GPU资源'/);
  assert.match(layout, /filter\([^\n]*gpu-resources[^\n]*user\?\.role === 'admin'/);
});
