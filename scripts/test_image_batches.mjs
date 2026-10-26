import assert from 'node:assert/strict';
import { imageBatches } from '../frontend/src/api/imageBatches.ts';

const ids=Array.from({length:2005},(_,i)=>String(i));
const sent=[],done=[];
assert.deepEqual(await imageBatches(ids,async batch=>{sent.push(batch);return batch.length;},batch=>done.push(...batch)),[1000,1000,5]);
assert.deepEqual(sent.flat(),ids);
assert.deepEqual(done,ids);
let remaining=[...ids],calls=0;
await assert.rejects(imageBatches(remaining,async()=>{if(++calls===2)throw Error('interrupted');return 1000;},batch=>{
  const completed=new Set(batch);remaining=remaining.filter(id=>!completed.has(id));
}),/interrupted/);
assert.equal(calls,2);assert.deepEqual(remaining,ids.slice(1000));
assert.deepEqual(await imageBatches(remaining,async batch=>batch.length),[1000,5]);
console.log('image batches: ordering, 1000 limit, stop on failure, remaining-only retry passed');
