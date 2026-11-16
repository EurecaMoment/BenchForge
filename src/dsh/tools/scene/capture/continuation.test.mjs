import assert from 'node:assert/strict';
import test from 'node:test';
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import {createCaptureContinuation} from './continuation.mjs';
import {captureContinuations,createCaptureHistory} from './continuation_state.mjs';
import {trackCaptureSubmission} from './submission.mjs';

const failure = (run='sf_scene', issue='dog_go2 has no recognizable asset', scene='apartment') => ({
  run_id:run,state:'FAILED_FINAL',terminal:true,tasks:[{id:run+'.scene0',state:'FAILED_FINAL',unit:{revision:0},
    failure:{next_action:'repair_complete_program_and_resubmit',scene_review:{passed:false,issues:[issue],
      production_acceptance:{passed:false,enforced:true,scene_id:scene}}},result:{directory:'/artifacts/'+run+'.scene0/revision_0'}}],
});
const running = {run_id:'sf_scene',state:'RUNNING',terminal:false,tasks:[]};
function harness({read=async()=>failure(),data=new Map(),events=[],artifacts='/artifacts',review=()=>null}={}) {
  const sent=[],errors=[],requests=[],abort=new AbortController();
  let seq=events.at(-1)?.seq||0;
  const append=(type,values)=>{const event={type,seq:++seq,time:seq*1000,data:values};events.push(event);return event;};
  const call=(name,value)=>{
    const callId='call-'+seq;
    append('tool/call',{name,callId,arguments:'{}'});
    append('tool/result',{message:{source:{callId},toolCallId:callId,content:[{type:'text',text:JSON.stringify(value)}]}});
  };
  const agent={id:'session-owner',status:'idle',session:{header:{cwd:'/workspace'},snapshotEvents:()=>events},
    inbox:{nextStep:[],nextTurn:[]},steer:message=>{sent.push(message);agent.inbox.nextStep.push(message);},
    followup:message=>{sent.push(message);agent.inbox.nextTurn.push(message);}};
  const consume=()=>{
    for(const message of [...agent.inbox.nextStep,...agent.inbox.nextTurn]) append('user/message',message);
    agent.inbox.nextStep=[];agent.inbox.nextTurn=[];
  };
  const table={get:key=>data.get(key),entries:()=>data.entries(),put:async(key,value)=>{data.set(key,structuredClone(value));},
    update:async(key,fn)=>{const value=fn(data.get(key));data.set(key,structuredClone(value));return value;}};
  const runtime=createCaptureContinuation({table,artifacts,signal:abort.signal,readReview:review,
    read:async(route,args,signal)=>{requests.push({route,args});return read(route,args,signal);},
    resolveAgent:async()=>agent,createMessage:value=>value,onError:error=>errors.push(error),delay:async()=>{}});
  const plan=()=>{append('turn/start',{turn:1});call('benchforge_pipeline_plan',{production_contract:'benchforge.production-stages/v1',state:'planned'});};
  const capture=(run='sf_scene')=>call('benchforge_capture',{run_id:run,task_id:run+'.scene0',state:'submitted'});
  const stop=()=>runtime.stopping({agent,signal:abort.signal});
  return {runtime,agent,append,call,consume,plan,capture,stop,sent,requests,data,events,errors,abort,
    dispose:async()=>{abort.abort();await runtime.dispose();assert.deepEqual(errors,[]);}};
}

test('failure after the model final response wakes the same idle conversation without a wait tool',async()=>{
  const terminal=Promise.withResolvers();let reads=0;
  const h=harness({read:async()=>++reads===1?running:terminal.promise});h.plan();h.capture();
  await h.runtime.reconcile(h.agent);
  await h.stop();
  h.append('turn/end',{turn:1,reason:{kind:'completed'}});
  assert.equal(h.sent.length,0);
  terminal.resolve(failure());await h.runtime.settled();
  assert.equal(h.sent.length,1);assert.equal(h.agent.inbox.nextTurn.length,1);
  assert.match(h.sent[0].content[0].text,/dog_go2 has no recognizable asset/);
  assert.match(h.sent[0].content[0].text,/保留原始硬要求/);
  assert.deepEqual(h.requests.map(row=>row.route),['/observe','/wait']);
  await h.dispose();
});

test('concurrent registrations observe one run once and preserve independent runs',async()=>{
  const h=harness({read:async(_route,args)=>failure(args.run_id)});
  await Promise.all(Array.from({length:8},()=>h.runtime.register(h.agent,{run_id:'sf_scene'})));
  await h.runtime.settled();
  assert.equal(h.requests.length,1);assert.equal(h.sent.length,1);
  await h.runtime.register(h.agent,{run_id:'sf_second'});await h.runtime.settled();
  assert.equal(h.sent.length,2);assert.equal(h.data.size,2);
  await h.dispose();
});

test('host reload restores an unfinished wait; consumed notices do not replay',async()=>{
  const entered=Promise.withResolvers();
  const h=harness({read:(_route,_args,signal)=>new Promise((resolve,reject)=>{
    entered.resolve();signal.addEventListener('abort',()=>reject(signal.reason),{once:true});
  })});h.plan();h.capture();await h.runtime.reconcile(h.agent);await entered.promise;
  await h.dispose();
  const next=harness({data:h.data,events:h.events});next.runtime.restore();await next.runtime.settled();
  assert.equal(next.sent.length,1);next.consume();await next.dispose();
  const restored=harness({data:next.data,events:next.events});restored.runtime.restore();await restored.runtime.settled();
  assert.equal(restored.sent.length,0);assert.equal(restored.requests.length,0);await restored.dispose();
});

test('a notice lost before log commit is redelivered after restart',async()=>{
  const h=harness();h.plan();h.capture();await h.runtime.reconcile(h.agent);await h.runtime.settled();await h.dispose();
  const next=harness({data:h.data,events:h.events});next.runtime.restore();await next.runtime.settled();
  assert.equal(next.sent.length,1);await next.dispose();
});

test('reload during an unfinished repair resumes once after the original notice was consumed',async()=>{
  const h=harness();h.plan();h.capture();await h.runtime.reconcile(h.agent);await h.runtime.settled();h.consume();
  h.append('turn/end',{turn:1,reason:{kind:'aborted',reason:{kind:'disposed'}}});await h.dispose();
  const next=harness({data:h.data,events:h.events});next.runtime.restore();await next.runtime.settled();
  assert.equal(next.sent.length,1);assert.match(next.sent[0].source.noticeKey,/:disposal:/);
  assert.equal(next.requests.length,0);next.consume();await next.dispose();
  const again=harness({data:next.data,events:next.events});again.runtime.restore();await again.runtime.settled();
  assert.equal(again.sent.length,0);await again.dispose();
});

test('explicit cancellation and a paused review park results until authorized continuation',async()=>{
  let state='paused';
  const h=harness({review:()=>({state})});h.plan();h.capture();await h.runtime.reconcile(h.agent);await h.runtime.settled();
  assert.equal(h.sent.length,0);assert.equal(h.runtime.records(h.agent.id)[0].state,'paused');
  state='elapsed';h.append('turn/start',{turn:2});h.append('user/message',{source:{kind:'benchforge/production-resume'}});
  await h.runtime.reconcile(h.agent);await h.runtime.settled();assert.equal(h.sent.length,1);await h.dispose();
  const cancelled=harness();cancelled.plan();cancelled.capture();
  cancelled.append('turn/end',{turn:1,reason:{kind:'aborted',reason:{kind:'user'}}});
  await cancelled.runtime.reconcile(cancelled.agent);assert.equal(cancelled.requests.length,0);await cancelled.dispose();
});

test('final text does not discharge repair obligations; duplicate queued reminders are suppressed',async()=>{
  const h=harness();h.plan();h.capture();await h.runtime.reconcile(h.agent);await h.runtime.settled();h.consume();
  h.append('assistant/message',{message:{content:[{type:'text',text:'下一步我会修复。'}]}});
  await h.stop();await h.stop();assert.equal(h.sent.length,2);
  h.consume();await h.stop();h.consume();await h.stop();
  assert.equal(h.runtime.records(h.agent.id)[0].state,'blocked');
  assert.match(h.sent.at(-1).content[0].text,/连续三次收尾/);
  h.consume();const count=h.sent.length;await h.stop();assert.equal(h.sent.length,count);
  await h.dispose();
});

test('pipeline completion releases capture obligations; unrelated follow-up is not restarted',async()=>{
  const h=harness();h.plan();h.capture();await h.runtime.reconcile(h.agent);await h.runtime.settled();h.consume();
  h.call('benchforge_pipeline_run',{state:'completed'});await h.stop();assert.equal(h.sent.length,1);
  h.append('turn/start',{turn:2});h.append('assistant/message',{message:{content:[{type:'text',text:'解释工作流程。'}]}});
  await h.stop();assert.equal(h.sent.length,1);await h.dispose();
});

test('retrying a settled service task tracks its new attempt and receives one new result',async()=>{
  const h=harness();await h.runtime.register(h.agent,{run_id:'sf_scene',call_id:'first'});await h.runtime.settled();h.consume();
  await h.runtime.register(h.agent,{run_id:'sf_scene',call_id:'retry',retry:true});await h.runtime.settled();
  assert.equal(h.sent.length,2);assert.notEqual(h.sent[0].source.noticeKey,h.sent[1].source.noticeKey);await h.dispose();
});

test('a retry supersedes an active observation without losing the new generation',async()=>{
  const entered=Promise.withResolvers();let reads=0,aborted=false;
  const h=harness({read:async(_route,_args,signal)=>{
    if(++reads>1)return failure();
    entered.resolve();
    return new Promise((resolve,reject)=>signal.addEventListener('abort',()=>{aborted=true;reject(signal.reason);},{once:true}));
  }});
  await h.runtime.register(h.agent,{run_id:'sf_scene',call_id:'first'});await entered.promise;
  await h.runtime.register(h.agent,{run_id:'sf_scene',call_id:'retry',retry:true});await h.runtime.settled();
  assert.equal(aborted,true);assert.equal(reads,2);assert.equal(h.sent.length,1);
  assert.match(h.sent[0].source.noticeKey,/:retry:FAILED_FINAL$/);await h.dispose();
});

test('observer errors are bounded; explicit recovery can deliver a distinct terminal result',async()=>{
  let available=false;
  const h=harness({read:async()=>{if(!available)throw Error('service temporarily unavailable');return failure();}});
  h.plan();h.capture();await h.runtime.reconcile(h.agent);await h.runtime.settled();
  assert.equal(h.requests.length,3);assert.equal(h.sent.length,1);h.consume();
  for(let i=0;i<4;i++)await h.runtime.reconcile(h.agent);
  await h.runtime.settled();assert.equal(h.requests.length,3);
  available=true;await h.runtime.register(h.agent,{run_id:'sf_scene'},{resume:true});await h.runtime.settled();
  assert.equal(h.sent.length,2);assert.equal(h.requests.length,4);
  assert.match(h.sent[0].source.noticeKey,/:observer_failed$/);
  assert.match(h.sent[1].source.noticeKey,/:FAILED_FINAL$/);await h.dispose();
});

test('new production objectives do not inherit or wake old capture obligations',async()=>{
  const terminal=Promise.withResolvers(),entered=Promise.withResolvers();
  const h=harness({read:async()=>{entered.resolve();return terminal.promise;}});
  h.plan();h.capture();await h.runtime.reconcile(h.agent);await entered.promise;
  h.call('benchforge_pipeline_plan',{production_contract:'benchforge.production-stages/v1',state:'planned',
    plan:'/workspace/new-plan.json',review_revision:{kind:'new_task'}});
  terminal.resolve(failure());await h.runtime.settled();await h.stop();
  assert.equal(h.sent.length,0);assert.equal(h.runtime.records(h.agent.id)[0].state,'settled');
  assert.equal(captureContinuations(h.events).captures.length,0);await h.dispose();
});

test('execution repairs retain ownership but a completed task is not resurrected on reload',async()=>{
  const h=harness();h.plan();h.capture();
  const before=captureContinuations(h.events);
  h.call('benchforge_pipeline_plan',{production_contract:'benchforge.production-stages/v1',state:'planned',
    plan:'/workspace/repaired-plan.json',review_revision:{kind:'execution_repair'}});
  const repaired=captureContinuations(h.events);
  assert.equal(repaired.taskSeq,before.taskSeq);assert.deepEqual(repaired.captures,before.captures);
  await h.runtime.reconcile(h.agent);await h.runtime.settled();h.consume();
  h.call('benchforge_pipeline_run',{production_contract:'benchforge.production-stages/v1',state:'completed'});
  await h.dispose();
  const next=harness({data:h.data,events:h.events});next.runtime.restore();await next.runtime.settled();
  assert.equal(next.sent.length,0);assert.equal(next.requests.length,0);await next.dispose();
});

test('checking an old stage does not complete production after a successful capture',async()=>{
  const h=harness({read:async()=>({run_id:'sf_scene',state:'SUCCEEDED',terminal:true,tasks:[]})});
  h.plan();h.capture();await h.runtime.reconcile(h.agent);await h.runtime.settled();h.consume();
  h.call('benchforge_stage_review',{stage:'S1',state:'elapsed'});await h.stop();
  assert.equal(h.sent.length,2);h.consume();
  h.call('benchforge_pipeline_run',{state:'waiting_user',stage:'S2'});await h.stop();
  assert.equal(h.sent.length,2);await h.dispose();
});

test('a service submission retains its identity if durable tracking cannot be registered',async()=>{
  const exec={agent:{id:'owner'},callId:'capture-call'};
  const tracking={register:async()=>{throw Error('storage write failed');}};
  const submitted=await trackCaptureSubmission({state:'accepted',run_id:'sf_scene',task_id:'sf_scene.scene0'},'/capture',{},exec,tracking);
  assert.equal(submitted.run_id,'sf_scene');assert.equal(submitted.state,'submitted');
  assert.equal(submitted.submission_accepted,true);assert.equal(submitted.scene_acceptance,'pending');
  assert.equal(submitted.watch.accepted,false);assert.match(submitted.next,/do not submit a duplicate/);
  const retried=await trackCaptureSubmission({task_id:'sf_scene.scene0'},'/retry',{},exec,tracking);
  assert.equal(retried.run_id,'sf_scene');assert.equal(Object.hasOwn(retried,'state'),false);
  assert.deepEqual(JSON.parse(JSON.stringify(retried)),retried);
});

test('quality failure cannot masquerade as an external blocker; unchanged evidence can stop repeated repairs',async()=>{
  const folder=await fs.mkdtemp(path.join(os.tmpdir(),'benchforge-continuation-'));
  const h=harness({artifacts:folder,read:async(_route,args)=>failure(args.run_id)});
  try {
    for(const run of ['sf_first','sf_second']) {
      await fs.mkdir(path.join(folder,run+'.scene0','revision_0'),{recursive:true});
      await fs.writeFile(path.join(folder,run+'.scene0','revision_0','scene_review.json'),'{}');
      await h.runtime.register(h.agent,{run_id:run,submitted_at:run==='sf_first'?100:200});await h.runtime.settled();
    }
    const args={run_id:'sf_second',kind:'external',reason:'The dog is still missing its articulated geometry.',
      missing_condition:'Need a recognizable imported robot with all legs.',evidence:[path.join(folder,'sf_second.scene0/revision_0/scene_review.json')]};
    await assert.rejects(h.runtime.block(h.agent,args),/not evidence of an external blocker/);
    const blocked=await h.runtime.block(h.agent,{...args,kind:'no_progress'});
    assert.equal(blocked.state,'blocked');assert.equal(blocked.accepted,false);
    await assert.rejects(h.runtime.block(h.agent,{...args,evidence:[path.join(folder,'sf_first.scene0/revision_0/scene_review.json')]}),/belong to this service capture/);
  } finally {
    await h.dispose();assert.equal(path.dirname(folder),os.tmpdir());assert.ok(path.basename(folder).startsWith('benchforge-continuation-'));
    await fs.rm(folder,{recursive:true,force:true});
  }
});

test('canonical metadata, PTC and incremental history preserve native ownership',()=>{
  const h=harness();h.plan();
  h.append('tool/ptc-dispatch',{name:'benchmarkforge_capture',subCallId:'inner',arguments:{},isError:false,
    content:[{type:'text',text:JSON.stringify({run_id:'sf_ptc',state:'submitted'})}]});
  const history=createCaptureHistory();const first=history(h.agent.session);
  assert.equal(first.captures.at(-1).run_id,'sf_ptc');assert.equal(history(h.agent.session),first);
  h.call('benchforge_retry',{task_id:'sf_ptc.scene0',state:'PENDING'});
  assert.equal(history(h.agent.session).captures.at(-1).retry,true);
  assert.deepEqual(history(h.agent.session),captureContinuations(h.events));
});
