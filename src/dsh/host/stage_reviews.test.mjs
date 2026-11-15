import assert from 'node:assert/strict';
import test from 'node:test';
import {createStageReviewClient} from './stage_reviews.mjs';

test('host review calls the shared CLI through the subprocess seam and releases its handle',async()=>{
  let spec,terminated=0,reaped=0;
  const subprocess={resolveExecutable:async()=>'/env/bin/python',spawn:value=>{
    spec=value;
    return {done:Promise.resolve({exitCode:0}),collected:{stdout:{readFrom:()=>({text:'{"state":"waiting_user","waits_completed":1}',lossy:false})}},
      terminate:()=>terminated++,waitForExit:async()=>{reaped++;}};
  }};
  const client=createStageReviewClient(subprocess,{python:'/env/bin/python'});
  const result=await client.advance('/workspace with spaces','session-test',3);
  assert.equal(result.waits_completed,1);
  assert.equal(spec.cwd,'/workspace with spaces');
  assert.ok(spec.argv.includes('server.stage_review_cli'));
  assert.deepEqual(JSON.parse(spec.argv.at(-1)),{action:'status',review_id:3});
  assert.equal(terminated,1);assert.equal(reaped,1);
  await client.dispose();
});

test('unloading the host aborts an in-flight review subprocess before disposal completes',async()=>{
  const spawned=Promise.withResolvers();let terminated=0,reaped=0;
  const subprocess={resolveExecutable:async()=>'/env/bin/python',spawn:spec=>{
    const done=new Promise((resolve,reject)=>spec.signal.addEventListener('abort',()=>reject(spec.signal.reason),{once:true}));
    spawned.resolve();
    return {done,terminate:()=>terminated++,waitForExit:async()=>{reaped++;}};
  }};
  const client=createStageReviewClient(subprocess);
  const rejected=assert.rejects(client.advance('/workspace','session-test',1),{name:'AbortError'});
  await spawned.promise;await client.dispose();await rejected;
  assert.ok(terminated>=1);assert.ok(reaped>=1);
});
