import test from 'node:test';
import assert from 'node:assert/strict';
import {draftHistory,changeDraft,undoDraft,redoDraft} from '../src/components/detectionEditor.ts';

test('box edits and negative marking preserve the current image description',()=>{
  const original={boxes:[{x:1,y:2,width:10,height:20,label:'car'}],negative:false,description:'人工核对的图片描述'};
  let history=draftHistory(original);
  history=changeDraft(history,{boxes:[{...original.boxes[0],x:3}],negative:false});
  assert.equal(history.present.description,original.description);
  history=changeDraft(history,{boxes:[],negative:true});
  assert.equal(history.present.description,original.description);
});

test('applying image descriptions participates in undo and redo without changing boxes',()=>{
  const original={boxes:[{x:1,y:2,width:10,height:20,label:'car'}],negative:false,description:'原描述'};
  const history=changeDraft(draftHistory(original),{...original,description:'大模型描述'});
  assert.deepEqual(history.present.boxes,original.boxes);
  assert.equal(undoDraft(history).present.description,'原描述');
  assert.equal(redoDraft(undoDraft(history)).present.description,'大模型描述');
});
