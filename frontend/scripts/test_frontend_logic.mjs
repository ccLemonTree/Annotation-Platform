import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';
import ts from 'typescript';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const frontendRoot = path.resolve(__dirname, '..');
const moduleCache = new Map();

function resolveTsPath(baseDir, specifier) {
  const raw = path.resolve(baseDir, specifier);
  const candidates = [
    raw,
    `${raw}.ts`,
    `${raw}.tsx`,
    path.join(raw, 'index.ts'),
  ];
  for (const candidate of candidates) {
    if (fs.existsSync(candidate) && fs.statSync(candidate).isFile()) return candidate;
  }
  throw new Error(`Cannot resolve module "${specifier}" from "${baseDir}"`);
}

function loadTsModule(relFromFrontend, mocks = {}) {
  const fullPath = path.isAbsolute(relFromFrontend)
    ? relFromFrontend
    : path.resolve(frontendRoot, relFromFrontend);
  const cacheKey = `${fullPath}::${Object.keys(mocks).sort().join(',')}`;
  if (moduleCache.has(cacheKey)) return moduleCache.get(cacheKey);

  const source = fs.readFileSync(fullPath, 'utf8');
  const transpiled = ts.transpileModule(source, {
    fileName: fullPath,
    compilerOptions: {
      target: ts.ScriptTarget.ES2023,
      module: ts.ModuleKind.CommonJS,
      jsx: ts.JsxEmit.ReactJSX,
      esModuleInterop: true,
    },
  });

  const mod = { exports: {} };
  moduleCache.set(cacheKey, mod.exports);

  const localRequire = (specifier) => {
    if (specifier in mocks) return mocks[specifier];
    if (specifier.startsWith('.')) {
      const target = resolveTsPath(path.dirname(fullPath), specifier);
      return loadTsModule(target, mocks);
    }
    throw new Error(`Unexpected external import "${specifier}" in pure logic test (${fullPath})`);
  };

  if (!globalThis.window) {
    globalThis.window = { location: { origin: 'http://192.168.1.115:3000', pathname: '/projects', search: '', hash: '' } };
  }
  const wrapper = `(function(exports, require, module, __filename, __dirname) {\n${transpiled.outputText}\n});`;
  const fn = vm.runInThisContext(wrapper, { filename: fullPath });
  fn(mod.exports, localRequire, mod, fullPath, path.dirname(fullPath));
  moduleCache.set(cacheKey, mod.exports);
  return mod.exports;
}

// Bridge helper mirroring WorkflowGraphBuilder.handleDeleteNode pure graph surgery
function bridgeDeleteNode(steps, edges, delKey) {
  if (steps.length <= 1) return { steps, edges };
  const incoming = edges.filter(e => e.target === delKey);
  const outgoing = edges.filter(e => e.source === delKey);
  const keptSteps = steps.filter(s => s.node_key !== delKey);
  const keptEdges = edges.filter(e => e.source !== delKey && e.target !== delKey);
  const bridged = [...keptEdges];

  const effectiveIn = incoming.length ? incoming : [{ source: '__input__', target: delKey, source_handle: 'default' }];
  for (const inE of effectiveIn) {
    for (const outE of outgoing) {
      if (inE.source === outE.target) continue;
      if (!bridged.some(e => e.source === inE.source && e.target === outE.target && e.source_handle === inE.source_handle)) {
        bridged.push({ source: inE.source, target: outE.target, source_handle: inE.source_handle });
      }
    }
  }
  return { steps: keptSteps, edges: bridged };
}

let passed = 0;
function test(name, fn) {
  fn();
  passed += 1;
  console.log(`  PASS  ${name}`);
}

console.log('Running frontend pure logic unit tests...');

// 1. Workflow DAG layout & graph normalization
const workflowGraph = loadTsModule('src/components/workflow/workflowGraph.ts', {
  '../../api/modelServices': { DEFAULT_DETECTION_CONFIDENCE: 0.25 },
});

test('workflowGraph.normalizeGraph builds linear edges and deduplicates keys', () => {
  const { steps, edges } = workflowGraph.normalizeGraph([
    { node_key: 'dup', operation: 'img_filter', title: '' },
    { node_key: 'dup', operation: 'crop', title: '切图' },
    { node_key: '', operation: 'box_clean' },
  ]);
  assert.deepEqual(steps.map(s => s.node_key), ['dup', 'dup_2', 'step_3']);
  assert.equal(steps[0].title, '图像基础规则过滤');
  assert.deepEqual(edges, [
    { source: 'dup', target: 'dup_2', source_handle: 'default' },
    { source: 'dup_2', target: 'step_3', source_handle: 'default' },
  ]);

  // Self-loops and dangling edges are stripped
  const filtered = workflowGraph.normalizeGraph(steps, [
    { source: '__input__', target: 'dup', source_handle: 'default' },
    { source: 'dup', target: 'dup', source_handle: 'default' },
    { source: 'dup', target: 'missing_node', source_handle: 'default' },
    { source: 'dup', target: 'step_3', source_handle: 'default' },
  ]);
  assert.deepEqual(filtered.edges, [
    { source: '__input__', target: 'dup', source_handle: 'default' },
    { source: 'dup', target: 'step_3', source_handle: 'default' },
  ]);
});

test('workflowGraph.computeGraphLayout assigns topological columns for diamond fan-in DAG & guards cycles', () => {
  const steps = [
    {
      node_key: 'n_branch',
      operation: 'branch',
      title: '分流',
      branch_config: {
        first_match: true,
        branches: [
          { key: 'b_person', name: '人', match_mode: 'all', crop_labels: ['person'] },
          { key: 'b_car', name: '车', match_mode: 'all', crop_labels: ['car'] },
        ],
      },
    },
    { node_key: 'n_person', operation: 'attributes', title: '人属性' },
    { node_key: 'n_car', operation: 'box_clean', title: '车清洗' },
    { node_key: 'n_merge', operation: 'ai_review', title: '汇流审核' },
  ];
  const edges = [
    { source: 'n_branch', target: 'n_person', source_handle: 'b_person' },
    { source: 'n_branch', target: 'n_car', source_handle: 'b_car' },
    { source: 'n_person', target: 'n_merge', source_handle: 'default' },
    { source: 'n_car', target: 'n_merge', source_handle: 'default' },
  ];

  const layout = workflowGraph.computeGraphLayout(steps, edges, true);
  const colByKey = new Map(layout.nodes.map(n => [n.key, n.col]));
  assert.equal(colByKey.get('__input__'), 0);
  assert.equal(colByKey.get('n_branch'), 1);
  assert.equal(colByKey.get('n_person'), 2);
  assert.equal(colByKey.get('n_car'), 2);
  assert.equal(colByKey.get('n_merge'), 3);

  const branchNode = layout.nodes.find(n => n.key === 'n_branch');
  assert.deepEqual(branchNode.handles.map(h => h.id), ['b_person', 'b_car', 'else']);

  // Guard against accidental draft cycles (must terminate cleanly)
  const cyclicLayout = workflowGraph.computeGraphLayout(
    steps.slice(1, 3),
    [
      { source: 'n_person', target: 'n_car', source_handle: 'default' },
      { source: 'n_car', target: 'n_person', source_handle: 'default' },
    ],
    false,
  );
  assert.equal(cyclicLayout.nodes.length, 2);
  assert.ok(cyclicLayout.width > 0 && cyclicLayout.height >= 210);
});

test('workflow node deletion bridges upstream handles to downstream targets without duplicates', () => {
  const steps = [
    { node_key: 'a', operation: 'crop', title: 'A' },
    { node_key: 'b', operation: 'box_clean', title: 'B' },
    { node_key: 'c', operation: 'ai_review', title: 'C' },
  ];
  const edges = [
    { source: 'a', target: 'b', source_handle: 'default' },
    { source: 'b', target: 'c', source_handle: 'default' },
  ];
  const afterMidDelete = bridgeDeleteNode(steps, edges, 'b');
  assert.deepEqual(afterMidDelete.steps.map(s => s.node_key), ['a', 'c']);
  assert.deepEqual(afterMidDelete.edges, [{ source: 'a', target: 'c', source_handle: 'default' }]);

  // Deleting entry node bridges implicit __input__ to next node
  const afterHeadDelete = bridgeDeleteNode(steps, edges, 'a');
  assert.deepEqual(afterHeadDelete.steps.map(s => s.node_key), ['b', 'c']);
  assert.deepEqual(afterHeadDelete.edges, [
    { source: 'b', target: 'c', source_handle: 'default' },
    { source: '__input__', target: 'b', source_handle: 'default' },
  ]);
});

// 2. Detection editor history, IoU duplicate detection & deterministic label colors
const detectionEditor = loadTsModule('src/components/detectionEditor.ts');

test('detectionEditor undo/redo stack & duplicate IoU pairs & labelColor', () => {
  let h = detectionEditor.draftHistory({ boxes: [], negative: false });
  const box1 = { x: 10, y: 10, width: 100, height: 100, label: 'person' };
  const box1NearDup = { x: 12, y: 12, width: 100, height: 100, label: 'person' };
  const boxCarSamePos = { x: 10, y: 10, width: 100, height: 100, label: 'car' };

  h = detectionEditor.changeDraft(h, { boxes: [box1], negative: false });
  // Identical draft should be a no-op
  const sameRef = detectionEditor.changeDraft(h, { boxes: [{ ...box1 }], negative: false });
  assert.equal(sameRef, h);

  h = detectionEditor.changeDraft(h, { boxes: [box1, box1NearDup, boxCarSamePos], negative: false });
  assert.deepEqual(detectionEditor.duplicatePairs(h.present.boxes), [[0, 1]]);

  const undone = detectionEditor.undoDraft(h);
  assert.equal(undone.present.boxes.length, 1);
  const redone = detectionEditor.redoDraft(undone);
  assert.equal(redone.present.boxes.length, 3);

  // Palette color vs deterministic fallback hash color
  const cKnown0 = detectionEditor.labelColor('person', ['person', 'car']);
  const cKnown1 = detectionEditor.labelColor('car', ['person', 'car']);
  assert.notEqual(cKnown0, cKnown1);
  assert.equal(detectionEditor.labelColor('helmet'), detectionEditor.labelColor('helmet'));
});

// 3. Pure-JS SHA-256 worker implementation matches Node native crypto SHA-256
const sha256Worker = loadTsModule('src/utils/sha256.worker.ts');

test('sha256.worker computeSha256Sync matches Node crypto across boundary sizes', () => {
  const sizes = [0, 1, 3, 55, 56, 63, 64, 65, 128, 4097];
  for (const size of sizes) {
    const buf = crypto.randomBytes(size);
    const ab = buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength);
    const expected = crypto.createHash('sha256').update(buf).digest('hex');
    const actual = sha256Worker.computeSha256Sync(ab);
    assert.equal(actual, expected, `SHA-256 mismatch at byte length ${size}`);
  }
});

// 4. Auth return path open-redirect & login-loop protection
const authNav = loadTsModule('src/utils/authNavigation.ts');

test('authNavigation.safeReturnPath blocks open redirects and login loops', () => {
  assert.equal(authNav.safeReturnPath('/projects/123/images?page=2#top'), '/projects/123/images?page=2#top');
  assert.equal(authNav.safeReturnPath('//evil.example.com/steal'), '/');
  assert.equal(authNav.safeReturnPath('https://evil.example.com'), '/');
  assert.equal(authNav.safeReturnPath('/\\evil.com'), '/');
  assert.equal(authNav.safeReturnPath('/login?next=/projects'), '/');
  assert.equal(authNav.safeReturnPath(''), '/');
});

// 5. AssetImage preview query param helper
const assetImageMod = loadTsModule('src/components/AssetImage.tsx', {
  react: { useEffect: () => {}, useState: (init) => [init, () => {}] },
  antd: { Image: () => null },
  '../api/client': { default: { get: async () => ({ data: null }) } },
  'react/jsx-runtime': { jsx: () => null, jsxs: () => null },
});

test('resolveAssetContentUrl appends ?preview=true by default for library images and omits for fullSize/assets', () => {
  assert.equal(assetImageMod.resolveAssetContentUrl({ imageId: 'img-1' }), '/api/images/img-1/content?preview=true');
  assert.equal(assetImageMod.resolveAssetContentUrl({ imageId: 'img-1', fullSize: true }), '/api/images/img-1/content');
  assert.equal(assetImageMod.resolveAssetContentUrl({ assetId: 'ast-9' }), '/api/assets/ast-9/content');
  assert.equal(assetImageMod.resolveAssetContentUrl({ contentUrl: '/custom/url' }), '/custom/url');
});

console.log(`All ${passed} frontend logic test suites passed.`);
