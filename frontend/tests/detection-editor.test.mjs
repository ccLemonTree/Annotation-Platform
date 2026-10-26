import test from 'node:test';
import assert from 'node:assert/strict';
import { draftHistory, changeDraft, undoDraft, redoDraft, hitBoxes, duplicatePairs, zoomView } from '../src/components/detectionEditor.ts';

const b = { x: 10, y: 10, width: 30, height: 40, label: '车' };
test('undo/redo restore move, class, delete and negative state; new edit clears redo', () => {
  let history = draftHistory({ boxes: [b], negative: false });
  history = changeDraft(history, { boxes: [{ ...b, x: 20, label: '人' }], negative: false });
  history = changeDraft(history, { boxes: [], negative: true });
  assert.deepEqual(undoDraft(history).present.boxes, [{ ...b, x: 20, label: '人' }]);
  assert.deepEqual(undoDraft(undoDraft(history)).present.boxes, [b]);
  assert.equal(redoDraft(undoDraft(history)).present.negative, true);
  assert.equal(changeDraft(undoDraft(history), { boxes: [b], negative: false }).future.length, 0);
});
test('no-op edits do not consume undo steps; history is bounded', () => {
  let history = draftHistory({ boxes: [b], negative: false });
  assert.equal(changeDraft(history, { boxes: [{ ...b }], negative: false }), history);
  for (let x = 0; x < 150; x++) history = changeDraft(history, { boxes: [{ ...b, x }], negative: false });
  assert.equal(history.past.length, 100);
});
test('overlap selection prefers smaller box and preserves both boxes', () => {
  const boxes = [{ ...b, width: 100, height: 100 }, b, { ...b, x: 200 }];
  assert.deepEqual(hitBoxes(boxes, { x: 20, y: 20 }), [1, 0]);
  assert.deepEqual(hitBoxes(boxes, { x: 0, y: 0 }), []);
  assert.equal(boxes.length, 3);
});
test('duplicate warnings only flag near-identical same-class rectangles', () => {
  assert.deepEqual(duplicatePairs([b, { ...b, x: 11 }, { ...b, label: '人' }]), [[0, 1]]);
  assert.deepEqual(duplicatePairs([b, { ...b, width: 1, height: 1 }]), []);
});
test('zoom keeps the image coordinate under the cursor and clamps scale', () => {
  const view = { x: 100, y: 50, width: 800, height: 600 };
  const point = { x: 300, y: 200 };
  const next = zoomView(view, point, 2, 800, 600);
  assert.equal((point.x - view.x) / view.width, (point.x - next.x) / next.width);
  assert.equal((point.y - view.y) / view.height, (point.y - next.y) / next.height);
  assert.equal(zoomView(view, point, 1000, 800, 600).width, 800 / 8);
  assert.equal(zoomView(view, point, .0001, 800, 600).width, 800 * 5);
});
