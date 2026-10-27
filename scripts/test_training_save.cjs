const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('../frontend/node_modules/typescript');

const source = fs.readFileSync(path.join(__dirname, '../frontend/src/pages/TrainingPage.tsx'), 'utf8');
const tree = ts.createSourceFile('TrainingPage.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
function find(predicate, node = tree) {
  if (predicate(node)) return node;
  return ts.forEachChild(node, child => find(predicate, child));
}
function compile(expression, scope) {
  const code = ts.transpileModule(`const callback = ${expression};`, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText;
  return new Function(...Object.keys(scope), `${code}; return callback;`)(...Object.values(scope));
}
const saveNode = find(node => ts.isVariableDeclaration(node) && node.name.getText(tree) === 'save');
const errorNode = find(node => ts.isFunctionDeclaration(node) && node.name?.text === 'errorText');
const errorText = compile(errorNode.getText(tree), {});
function harness({ editing, fail, catalog = { rfdetr_2xlarge: { defaults: { epochs: 100, batch_size: 1 }, environment: {} } } } = {}) {
  const state = {}, requests = [];
  const set = key => value => { state[key] = value; };
  const send = async (id, input) => {
    requests.push({ id, input });
    if (fail) throw fail;
    return { id: id || 'new-run', config: input };
  };
  const save = compile(saveNode.initializer.getText(tree), {
    setSaving: set('saving'), setError: set('pageError'), setSaveError: set('saveError'),
    catalog, kind: 'rfdetr_2xlarge', source: 'remote', swift: false, editing,
    createTrainingRun: input => send(undefined, input), updateTrainingRun: send,
    setOpen: set('open'), setSelected: set('selected'), setDetailTab: set('tab'),
    setDetailError: set('detailError'), setReload: () => {}, errorText,
  });
  return { state, requests, save };
}
const values = { name: '烟火训练', dataset_path: '/ai/chen/training/datasets/middle',
  gpu_uuids: ['GPU-a', 'GPU-b'], parameters: { epochs: 50, batch_size: 10 }, shm_size_gb: 128 };

test('invalid form reports why saving was blocked and enables locating the field', () => {
  const form = find(node => ts.isJsxOpeningElement(node) && node.tagName.getText(tree) === 'Form');
  const attr = name => form.attributes.properties.find(p => p.name?.getText(tree) === name);
  assert.ok(attr('onFinishFailed'), 'Missing feedback for blocked form submission');
  assert.ok(attr('scrollToFirstError'), 'Missing navigation to the invalid field');
  let message;
  const handler = compile(attr('onFinishFailed').initializer.expression.getText(tree), { setSaveError: value => { message = value; } });
  handler({ errorFields: [{ name: ['gpu_uuids'], errors: ['请选择至少一张物理GPU'] }] });
  assert.match(message, /物理GPU/);
});

test('request preparation failure releases saving state and remains visible', async () => {
  const h = harness({ catalog: {} });
  await h.save(values);
  assert.equal(h.state.saving, false);
  assert.ok(h.state.saveError);
  assert.equal(h.requests.length, 0);
});

test('API failure uses independent save feedback that list refresh cannot clear', async () => {
  const h = harness({ fail: { response: { data: { detail: '服务器连接已停用' } } } });
  await h.save(values);
  assert.equal(h.state.saving, false);
  assert.equal(h.state.saveError, '服务器连接已停用');
  h.state.pageError = '';
  assert.equal(h.state.saveError, '服务器连接已停用');
  const alert = find(node => ts.isJsxSelfClosingElement(node) && node.tagName.getText(tree) === 'Alert'
    && node.attributes.properties.some(p => p.name?.getText(tree) === 'title' && p.initializer?.getText(tree) === '{saveError}'));
  assert.ok(alert, 'Missing dedicated save feedback in the form');
});

test('backend field validation errors are readable', () => {
  assert.match(errorText({ response: { data: { detail: [{ loc: ['body', 'dataset_path'], msg: '数据路径无效' }] } } }), /数据路径无效/);
});

for (const editing of [undefined, 'existing-run']) {
  test(`valid ${editing ? 'edit' : 'create'} preserves the supplied configuration`, async () => {
    const h = harness({ editing });
    await h.save(values);
    assert.equal(h.state.saving, false);
    assert.equal(h.state.open, false);
    assert.equal(h.state.selected.id, editing || 'new-run');
    assert.equal(h.requests.length, 1);
    assert.equal(h.requests[0].input.parameters.epochs, 50);
    assert.equal(h.requests[0].input.parameters.batch_size, 10);
    assert.equal(h.requests[0].input.shm_size_gb, 128);
  });
}

test('copying a canceled run prefills a create form and preserves the original configuration', () => {
  const node=find(node=>ts.isVariableDeclaration(node)&&node.name.getText(tree)==='showEdit');
  const state={},set=key=>value=>{state[key]=value;};
  const form={resetFields:()=>{},setFieldsValue:values=>{state.values=values;}};
  const showEdit=compile(node.initializer.getText(tree),{
    setSaveError:set('saveError'),setEditing:set('editing'),setError:set('error'),setKind:set('kind'),setServerId:set('server'),
    setSource:set('source'),setGpuLoading:set('gpuLoading'),setGpuError:set('gpuError'),setGpus:set('gpus'),setSnapshots:set('snapshots'),
    setDatasetLabels:set('labels'),form,setOpen:set('open'),setSnapshotLoading:set('snapshotLoading'),
    getTrainingSnapshots:()=>Promise.resolve([]),errorText,
  });
  const original={id:'canceled-id',name:'原训练',kind:'rfdetr_2xlarge',status:'canceled',server:{id:'server-a'},
    source:{kind:'snapshot',id:'snapshot-a',project_id:'project-a',labels:['ele','bike']},
    config:{name:'原训练',kind:'rfdetr_2xlarge',snapshot_id:'snapshot-a',selected_labels:['bike'],model_path:'/models/base.pth',
      gpu_uuids:['GPU-a','GPU-b'],parameters:{epochs:50,batch_size:64},environment:{CUDA_VISIBLE_DEVICES:'0,1'},mounts:[],
      output_root:'/runs/custom',output_subdir:'train',adapter_version:1,runtime_dataset:'/output/dataset/rfdetr'}};
  const before=JSON.stringify(original);
  showEdit(original,true);
  assert.equal(state.editing,undefined);
  assert.equal(state.values.name,'原训练 副本');
  assert.equal(state.source,'snapshot');assert.equal(state.open,true);
  for(const key of ['snapshot_id','selected_labels','model_path','gpu_uuids','parameters','environment','mounts','output_root'])
    assert.deepEqual(state.values[key],original.config[key],key);
  assert.equal(state.values.server_id,'server-a');assert.equal(state.values.project_id,'project-a');
  assert.ok(!('adapter_version' in state.values)&&!('runtime_dataset' in state.values));
  state.values.parameters.epochs=999;state.values.gpu_uuids.push('GPU-c');
  assert.equal(JSON.stringify(original),before);
});
