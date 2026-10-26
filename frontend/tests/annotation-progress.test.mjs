import test from 'node:test';
import assert from 'node:assert/strict';
import { readAnnotationProgress, rememberAnnotationProgress, clearAnnotationProgress } from '../src/components/annotationProgress.ts';

const values = new Map();
globalThis.localStorage = {
  getItem: key => values.get(key) ?? null,
  setItem: (key, value) => values.set(key, value),
  removeItem: key => values.delete(key),
};
const asset = { id: 'aaaaaaaa-0000-4000-8000-000000000001', project_id: 'project-a', original_name: 'image.jpg' };
test('progress survives reread and is isolated by user, tenant and project', () => {
  values.clear();
  localStorage.setItem('user', JSON.stringify({ id: 1, tenant_id: 1 }));
  assert.equal(rememberAnnotationProgress(asset), true);
  assert.deepEqual(readAnnotationProgress('project-a'), { assetId: asset.id, name: 'image.jpg' });
  assert.equal(readAnnotationProgress('project-b'), null);
  for (const user of [{ id: 2, tenant_id: 1 }, { id: 1, tenant_id: 2 }]) {
    localStorage.setItem('user', JSON.stringify(user));
    assert.equal(readAnnotationProgress('project-a'), null);
  }
  localStorage.setItem('user', JSON.stringify({ id: 1, tenant_id: 1 }));
  assert.equal(readAnnotationProgress('project-a').assetId, asset.id);
  clearAnnotationProgress('project-a');
  assert.equal(readAnnotationProgress('project-a'), null);
});
test('broken or inaccessible browser storage does not break the workbench', () => {
  for (const user of ['bad JSON', 'null', '{}']) {
    localStorage.setItem('user', user);
    assert.equal(rememberAnnotationProgress(asset), false);
    assert.equal(readAnnotationProgress('project-a'), null);
  }
  localStorage.setItem('user', JSON.stringify({ id: 1, tenant_id: 1 }));
  rememberAnnotationProgress(asset);
  const key = [...values.keys()].find(key => key.startsWith('annotation-progress:'));
  for (const value of ['{', 'null', '{"assetId":"invalid","name":"bad"}']) {
    values.set(key, value);
    assert.equal(readAnnotationProgress('project-a'), null);
  }
  const original = globalThis.localStorage;
  globalThis.localStorage = { getItem() { throw Error('storage denied'); } };
  assert.equal(rememberAnnotationProgress(asset), false);
  assert.equal(readAnnotationProgress('project-a'), null);
  clearAnnotationProgress('project-a');
  globalThis.localStorage = original;
});
