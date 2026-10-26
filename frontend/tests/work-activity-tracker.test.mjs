import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

function harness() {
  const file = new URL('../src/components/WorkActivityTracker.tsx', import.meta.url);
  assert.ok(fs.existsSync(file), 'workbench activity tracker is missing');
  const source = ts.transpileModule(fs.readFileSync(file, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022 },
  }).outputText + '\nexports.startActivityTracking = startActivityTracking;';
  let now = 1000000, callback, fail = false;
  const listeners = new Map(), requests = [], options = new Map();
  const document = { visibilityState: 'visible', hasFocus: () => focused };
  let focused = true;
  const window = {
    addEventListener(name, handler, config) { listeners.set(name, handler); options.set(name, config); },
    removeEventListener(name) { listeners.delete(name); },
    setInterval(fn) { callback = fn; return 1; }, clearInterval() { callback = null; },
  };
  document.addEventListener = window.addEventListener;
  document.removeEventListener = window.removeEventListener;
  const exports = {};
  vm.runInNewContext(source, {
    exports, window, document, crypto: { randomUUID: () => 'session-test' },
    Date: class extends Date { static now() { return now; } },
    require(name) {
      if (name.includes('api/analytics')) return { postActivity: async data => {
        requests.push(structuredClone(data)); if (fail) throw Error('offline');
        return { accepted_seconds: 15, duplicate: false };
      } };
      return {};
    },
  });
  const status = [];
  const stop = exports.startActivityTracking({ projectId: 'p', assetId: 'a', activity: 'annotation' }, s => status.push(s));
  return { requests, status, stop,
    capture(name) { return options.get(name)?.capture; },
    event(name) { listeners.get(name)?.({ isTrusted: true }); },
    async tick(ms = 15000) { now += ms; callback?.(); await new Promise(resolve => setImmediate(resolve)); },
    async advance(ms) { now += ms; await new Promise(resolve => setImmediate(resolve)); },
    hidden(value) { document.visibilityState = value ? 'hidden' : 'visible'; },
    focus(value) { focused = value; }, fail(value) { fail = value; },
  };
}

test('no input means no time; interaction starts short activity heartbeats', async () => {
  const h = harness(); await h.tick(); assert.equal(h.requests.length, 0);
  h.event('pointerdown'); await h.tick();
  assert.equal(h.requests.length, 1);
  const data = h.requests[0];
  assert.equal(data.sequence, 1); assert.equal(data.activity, 'annotation');
  assert.equal(Date.parse(data.ended_at)-Date.parse(data.started_at), 15000);
  h.stop();
});

test('activity capture observes canvas keyboard/pointer events before propagation is stopped', () => {
  const h = harness();
  for (const name of ['keydown', 'pointerdown', 'pointermove', 'wheel']) assert.equal(h.capture(name), true);
  h.stop();
});

test('hidden and unfocused windows pause; idle time is bounded and resumes only on input', async () => {
  const h = harness(); h.event('keydown'); await h.tick();
  h.hidden(true); h.event('visibilitychange'); await h.tick(90000);
  assert.equal(h.requests.length, 1);
  h.hidden(false); h.event('visibilitychange'); await h.tick();
  assert.equal(h.requests.length, 1);
  h.event('pointermove'); await h.tick();
  h.focus(false); h.event('blur'); await h.tick();
  assert.equal(h.requests.length, 2);
  h.focus(true); h.event('keydown');
  for (let i = 0; i < 6; i++) await h.tick();
  assert.equal(h.requests.length, 6); // Exactly 60 seconds since last input.
  h.event('keydown'); await h.tick();
  assert.equal(h.requests.length, 7); h.stop();
});

test('failed heartbeat retries the same session and sequence; cleanup flushes remaining time', async () => {
  const h = harness(); h.fail(true); h.event('pointerdown'); await h.tick();
  const original = h.requests[0]; h.fail(false); await h.tick();
  assert.deepEqual(h.requests[1], original);
  assert.equal(h.requests[2].sequence, 2);
  await h.advance(3000); h.stop(); await new Promise(resolve => setImmediate(resolve));
  assert.equal(h.requests.at(-1).sequence, 3);
  assert.equal(Date.parse(h.requests.at(-1).ended_at)-Date.parse(h.requests.at(-1).started_at), 3000);
});
