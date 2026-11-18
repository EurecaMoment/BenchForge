import assert from 'node:assert/strict';
import test from 'node:test';
import {createStageReviewClient} from './stage_reviews.mjs';
import path from 'node:path';

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

test('review config is resolved at Host creation, before changing to the workspace', async () => {
  let spec;
  const subprocess = {resolveExecutable: async () => 'python', spawn: value => {
    spec = value;
    return {done: Promise.resolve({exitCode: 0}),
      collected: {stdout: {readFrom: () => ({text: '{"state":"waiting_user"}'})}},
      terminate() {}, async waitForExit() {}};
  }};
  const client = createStageReviewClient(subprocess, {productionConfig: 'config/instance.json'});
  await client.advance('/another/workspace', 'session-a', 4);
  assert.equal(spec.argv[spec.argv.indexOf('--config') + 1], path.resolve('config/instance.json'));
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

test('review deadline covers both a stalled subprocess and its stale systemd scope',async()=>{
  const reports=[];
  const subprocess={resolveExecutable:async()=>'/python',spawn:()=>({done:new Promise(()=>{}),terminate(){},waitForExit:()=>new Promise(()=>{})})};
  const client=createStageReviewClient(subprocess,{reviewTimeoutMs:15,cleanupTimeoutMs:15,onCleanupError:error=>reports.push(error)});
  await assert.rejects(client.advance('/workspace','session-test',1),{name:'TimeoutError'});
  assert.equal(reports[0].code,'BENCHFORGE_PROCESS_CLEANUP_PENDING');
  await assert.rejects(client.dispose(),AggregateError);
});

test('user cancellation is propagated to a running background review',async()=>{
  const spawned=Promise.withResolvers(),controller=new AbortController();let signal;
  const subprocess={resolveExecutable:async()=>'/python',spawn:spec=>{
    signal=spec.signal;spawned.resolve();return {done:new Promise(()=>{}),terminate(){},waitForExit:async()=>true};
  }};
  const client=createStageReviewClient(subprocess),operation=client.advance('/workspace','session-test',1,0,controller.signal);
  const stopped=assert.rejects(operation,{name:'AbortError'});
  await spawned.promise;controller.abort(new DOMException('User stopped','AbortError'));await stopped;
  assert.equal(signal.aborted,true);await client.dispose();
});
