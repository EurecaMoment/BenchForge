import assert from 'node:assert/strict';
import test from 'node:test';
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import {setImmediate as nextTick} from 'node:timers/promises';
import {createStageReviewReader} from '../../../production/reviews.mjs';
import {createCaptureContinuation} from './continuation.mjs';
import {captureContinuations,createCaptureHistory,repeatedRepairFailure} from './continuation_state.mjs';
import {repairEvidence,repairImageContent} from './repair_evidence.mjs';
import {trackCaptureSubmission} from './submission.mjs';
import {captureNotice} from './notice.mjs';
import {createDeliveryReview} from './quality_review.mjs';
import {createProductionLifecycle} from '../../../production/lifecycle.mjs';

/** 构造带场景质量诊断的最终失败观察结果，允许指定运行和具体问题。 */
const failure = (run='sf_scene', issue='dog_go2 has no recognizable asset', scene='apartment') => ({
  run_id:run,state:'FAILED_FINAL',terminal:true,tasks:[{id:run+'.scene0',state:'FAILED_FINAL',unit:{revision:0},
    failure:{next_action:'repair_complete_program_and_resubmit',scene_review:{passed:false,issues:[issue],
      production_acceptance:{passed:false,enforced:true,scene_id:scene}}},result:{directory:'/artifacts/'+run+'.scene0/revision_0'}}],
});
const running = {run_id:'sf_scene',state:'RUNNING',terminal:false,tasks:[]};
/** 创建可控事件历史、持久化续接表和采集服务，收集通知、请求与错误。 */
function harness({read=async()=>failure(),data=new Map(),events=[],artifacts='/artifacts',review=()=>null}={}) {
  const sent=[],errors=[],requests=[],abort=new AbortController();
  let seq=events.at(-1)?.seq||0;
  /** 把原生事件加入测试历史并分配连续序号及确定性时间。 */
  const append=(type,values)=>{const event={type,seq:++seq,time:seq*1000,data:values};events.push(event);return event;};
  /** 以标准工具调用和结果事件记录一次采集业务回执。 */
  const call=(name,value)=>{
    const callId='call-'+seq;
    append('tool/call',{name,callId,arguments:'{}'});
    append('tool/result',{message:{source:{callId},toolCallId:callId,content:[{type:'text',text:JSON.stringify(value)}]}});
  };
  const agent={id:'session-owner',status:'idle',session:{header:{cwd:'/workspace'},/** 返回夹具当前原生历史，供续接上下文增量归约。 */ snapshotEvents:()=>events},
    inbox:{nextStep:[],nextTurn:[]},/** 记录当前步骤收到的引导消息，并加入测试收件箱。 */ steer:message=>{sent.push(message);agent.inbox.nextStep.push(message);},
    /** 记录下一轮收到的跟进消息，并加入测试收件箱。 */
    followup:message=>{sent.push(message);agent.inbox.nextTurn.push(message);}};
  /** 把待处理通知写入原生用户消息历史，再清空测试收件箱。 */
  const consume=()=>{
    for(const message of [...agent.inbox.nextStep,...agent.inbox.nextTurn]) append('user/message',message);
    agent.inbox.nextStep=[];agent.inbox.nextTurn=[];
  };
  const table={/** 按测试键读取内存记录或模拟服务。 */ get:key=>data.get(key),/** 返回测试内存表中的全部记录，供恢复与遍历逻辑使用。 */ entries:()=>data.entries(),/** 保存测试记录的副本，模拟持久化写入并防止引用共享。 */ put:async(key,value)=>{data.set(key,structuredClone(value));},
    /** 按更新函数修改持久化记录并保存副本，模拟原子表更新。 */
    update:async(key,fn)=>{const value=fn(data.get(key));data.set(key,structuredClone(value));return value;}};
  const runtime=createCaptureContinuation({table,artifacts,signal:abort.signal,readReview:review,
    /** 收集采集服务路由与参数，再转给测试传入的可控读取器。 */
    read:async(route,args,signal)=>{requests.push({route,args});return read(route,args,signal);},
    /** 返回持有采集任务的测试 Agent，模拟重载后的原生恢复。 */
    resolveAgent:async()=>agent,/** 保留测试消息结构，便于断言实际投递内容。 */ createMessage:value=>value,/** 收集后台续接错误，供测试在结束时确认没有遗漏异常。 */ onError:error=>errors.push(error),/** 立即完成测试退避等待，使故障重试无需真实计时。 */ delay:async()=>{}});
  /** 写入原生轮次开始和生产计划回执，建立续接所属的生产任务。 */
  const plan=()=>{append('turn/start',{turn:1});call('benchforge_pipeline_plan',{production_contract:'benchforge.production-stages/v1',state:'planned'});};
  /** 写入指定运行的采集提交回执，模拟模型已经成功启动任务。 */
  const capture=(run='sf_scene')=>call('benchforge_capture',{run_id:run,task_id:run+'.scene0',state:'submitted'});
  /** 以夹具取消信号触发生产停止前的采集协调。 */
  const stop=()=>runtime.stopping({agent,signal:abort.signal});
  return {runtime,agent,append,call,consume,plan,capture,stop,sent,requests,data,events,errors,abort,
    /** 取消并等待后台观察退出，再断言没有未处理的续接异常。 */
    dispose:async()=>{abort.abort();await runtime.dispose();assert.deepEqual(errors,[]);}};
}

test('failure after the model final response wakes the same idle conversation without a wait tool',/** 验证采集后台观察、状态恢复和通知投递在该用例中的具体边界，完成准备、调用和结果断言。用例：failure after the model final response wakes the same idle conversation without a wait tool。 */ async()=>{
  const terminal=Promise.withResolvers();let reads=0;
  const h=harness({/** 提供该用例定义的可控采集服务响应，模拟等待、终态、取消或短暂不可用。 */ read:async()=>++reads===1?running:terminal.promise});h.plan();h.capture();
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

test('concurrent registrations observe one run once and preserve independent runs',/** 验证采集后台观察、状态恢复和通知投递在该用例中的具体边界，完成准备、调用和结果断言。用例：concurrent registrations observe one run once and preserve independent runs。 */ async()=>{
  const h=harness({/** 提供该用例定义的可控采集服务响应，模拟等待、终态、取消或短暂不可用。 */ read:async(_route,args)=>failure(args.run_id)});
  await Promise.all(Array.from({length:8},()=>h.runtime.register(h.agent,{run_id:'sf_scene'})));
  await h.runtime.settled();
  assert.equal(h.requests.length,1);assert.equal(h.sent.length,1);
  await h.runtime.register(h.agent,{run_id:'sf_second'});await h.runtime.settled();
  assert.equal(h.sent.length,2);assert.equal(h.data.size,2);
  await h.dispose();
});

test('a rejected eleventh capture stops older prompts for the same frozen scene',async t=>{
  const budget={scene_id:'apartment',scene_design_id:'design',scene_key:'frozen-one',used:81,limit:10,remaining:0,
    evidence:[{task_id:'sf_historical.scene0',revision:0,token:'old-token',captured:true,accepted:true}]};
  const code='S2_CAPTURE_BUDGET_EXHAUSTED';
  const rejection={code,issues:[{code,capture_budget:budget}]};
  const h=harness({read:async(_route,{run_id})=>{
    const raw=failure(run_id);
    raw.tasks[0].capture_scene_key=run_id==='sf_scene'?'frozen-one':'frozen-two';
    return raw;
  }});
  t.after(()=>h.dispose());h.plan();h.capture();
  await h.runtime.reconcile(h.agent);await h.runtime.settled();h.consume();
  h.append('tool/ptc-dispatch',{name:'benchforge_capture',subCallId:'blocked',isError:true,
    content:[{type:'text',text:'Error: '+JSON.stringify(rejection)}]});
  assert.deepEqual(h.runtime.currentBudget(h.agent),budget);
  await h.stop();await h.runtime.reconcile(h.agent);await h.runtime.settled();
  assert.equal(h.sent.length,1);

  h.capture('sf_independent');
  await h.runtime.reconcile(h.agent);await h.runtime.settled();h.consume();
  assert.equal(h.runtime.currentBudget(h.agent),undefined);
  await h.stop();
  assert.equal(h.sent.at(-1).source.kind,'benchforge/capture-continuation');
  assert.match(h.sent.at(-1).content[0].text,/sf_independent/);
});

test('delivery review uses retained evidence after the matching scene is exhausted',async t=>{
  const code='S2_CAPTURE_BUDGET_EXHAUSTED';
  const capture_budget={scene_id:'apartment',scene_key:'frozen-one',used:10,limit:10,remaining:0,evidence:[]};
  const h=harness();t.after(()=>h.dispose());h.plan();h.capture();
  h.append('tool/ptc-dispatch',{name:'benchforge_capture',subCallId:'refused',isError:true,
    content:[{type:'text',text:'Error: '+JSON.stringify({code,capture_budget})}]});
  const task={id:'sf_scene.scene0',state:'SUCCEEDED',capture_scene_key:'frozen-one',unit:{intent:{},revision:0},
    result:{operation:'capture_only',directory:'/artifacts/sf_scene.scene0/revision_0',report:{status:'captured'}}};
  const review=createDeliveryReview({api:async()=>({tasks:[task]}),createMessage:value=>value});
  await review({agent:h.agent,turn:1,signal:h.abort.signal});
  assert.match(h.sent.at(-1).content[0].text,/capture budget.*exhausted/);
  assert.doesNotMatch(h.sent.at(-1).content[0].text,/submit one revised capture/);
  task.capture_scene_key='frozen-two';
  await review({agent:h.agent,turn:1,signal:h.abort.signal});
  assert.match(h.sent.at(-1).content[0].text,/submit one revised capture/);
});

const layout = (run, state='RUNNING') => ({run_id:run,state,terminal:state!=='RUNNING',
  tasks:[{id:run+'.scene0',state,workflow:'layout'}]});

test('production stops while an explicit owned layout wait is live and resumes normal limits after it settles',async t=>{
  const terminal=Promise.withResolvers();
  const h=harness({read:()=>terminal.promise});h.plan();
  t.after(async()=>{terminal.resolve(layout('sf_layout','SUCCEEDED'));await h.dispose();});
  const view={active:true,pendingCaptures:[{run:'sf_layout',name:'布局'}],turn:1,workflowTurn:1,state:'running',plan:'/workspace/plan.json',workspace:'/workspace',run:'',revision:1};
  const lifecycle=createProductionLifecycle({projection:{stateOf:()=>({view,notices:[],lastResponse:{seq:h.events.length,visible:false}})},
    table:{get:()=>undefined,put:()=>{}},createMessage:value=>value,hasPendingWait:h.runtime.hasPendingWait});
  t.after(()=>lifecycle.dispose());

  const wait=await h.runtime.wait(h.agent,'sf_layout',layout('sf_layout'));
  assert.equal(wait.watch.accepted,true);
  h.call('benchforge_wait',wait);
  await lifecycle.stopping({agent:h.agent,turn:1});
  assert.equal(h.sent.length,1);
  assert.match(h.sent[0].source.summary,/布局.*sf_layout/);
  await lifecycle.stopping({agent:h.agent,turn:1});
  assert.equal(h.sent.length,1);
  terminal.resolve(layout('sf_layout','SUCCEEDED'));await h.runtime.settled();
  assert.equal(h.sent.length,2);
  assert.equal(h.sent[1].source.capture.workflow,'layout');
  assert.equal(h.sent[1].source.capture.state,'SUCCEEDED');
  view.pendingCaptures=[];
  h.consume();
  for(let attempt=0;attempt<2;attempt++){
    h.append('assistant/message',{message:{content:[]}});
    await lifecycle.stopping({agent:h.agent,turn:2});h.consume();
  }
  h.append('assistant/message',{message:{content:[]}});
  await assert.rejects(lifecycle.stopping({agent:h.agent,turn:2}),{code:'BENCHFORGE_PRODUCTION_INCOMPLETE'});
});

test('explicit waits keep distinct outcomes across reload and cancellation',async t=>{
  const read=(_route,_args,signal)=>new Promise((_resolve,reject)=>signal.addEventListener('abort',()=>reject(signal.reason),{once:true}));
  const h=harness({read});h.plan();
  await assert.rejects(h.runtime.wait(h.agent,'sf_foreign',layout('sf_other')),/identity/);
  assert.equal((await h.runtime.wait(h.agent,'sf_old',layout('sf_old','SUCCEEDED'))).watch,undefined);
  for(const run of ['sf_layout','sf_second','sf_cancelled'])
    h.call('benchforge_wait',await h.runtime.wait(h.agent,run,layout(run)));
  assert.equal(h.runtime.hasPendingWait(h.agent),true);
  assert.equal(h.runtime.hasPendingWait({...h.agent,id:'another-session'}),false);
  h.call('benchforge_cancel',{run_id:'sf_cancelled',cancel_requested:true});
  const outcomes=new Map([['sf_layout','SUCCEEDED'],['sf_second','FAILED_FINAL'],['sf_cancelled','CANCELED']]);
  await h.dispose();

  const next=harness({data:h.data,events:h.events,read:async(_route,args)=>layout(args.run_id,outcomes.get(args.run_id))});
  t.after(()=>next.dispose());
  next.runtime.restore();
  await next.runtime.settled();
  assert.equal(next.runtime.hasPendingWait(next.agent),false);
  assert.deepEqual(next.sent.map(message=>message.source.capture.state).sort(),['FAILED_FINAL','SUCCEEDED']);
  next.consume();
  next.runtime.restore();await next.runtime.settled();
  assert.equal(next.sent.length,2);
});

test('capture reconciliation and stopping can be cancelled during a stalled backend read',/** 验证采集后台观察、状态恢复和通知投递在该用例中的具体边界，完成准备、调用和结果断言。用例：capture reconciliation and stopping can be cancelled during a stalled backend read。 */ async t=>{
  const reader=createStageReviewReader({/** 提供用例控制的服务响应，覆盖正常返回、超时和取消等读取边界。 */ service:()=>new Promise(()=>{})});
  const h=harness({review:reader.latest});h.plan();h.capture();
  t.after(()=>h.dispose());
  for(const operation of ['reconcile','stopping']){
    const controller=new AbortController();
    const pending=operation==='reconcile'?h.runtime.reconcile(h.agent,{signal:controller.signal}):
      h.runtime.stopping({agent:h.agent,signal:controller.signal});
    const rejected=assert.rejects(pending,{name:'AbortError'});
    await nextTick();controller.abort();await rejected;
    assert.equal(h.requests.length,0);assert.equal(h.sent.length,0);assert.equal(h.data.size,0);
  }
});

test('a review read delayed past cancellation, completion or a new task cannot deliver an old capture',/** 验证采集后台观察、状态恢复和通知投递在该用例中的具体边界，完成准备、调用和结果断言。用例：a review read delayed past cancellation, completion or a new task cannot deliver an old capture。 */ async()=>{
  for(const change of ['cancel','complete','new-task']){
    const entered=Promise.withResolvers(),released=Promise.withResolvers();let reads=0;
    const h=harness({/** 提供该用例的可控复核状态，并允许在读取边界制造并发变化。 */ review:async()=>{if(++reads===3){entered.resolve();await released.promise;}return null;}});
    h.plan();h.capture();
    try{
      await h.runtime.reconcile(h.agent);await entered.promise;
      if(change==='cancel')h.append('turn/end',{turn:1,reason:{kind:'aborted',reason:{kind:'user'}}});
      else if(change==='complete')h.call('benchforge_pipeline_run',{state:'completed'});
      else h.call('benchforge_pipeline_plan',{production_contract:'benchforge.production-stages/v1',plan:'/workspace/new.json',review_revision:{kind:'new_task'}});
      released.resolve();await h.runtime.settled();assert.equal(h.sent.length,0,change);
    }finally{released.resolve();await h.dispose();}
  }
});

test('host reload restores an unfinished wait; consumed notices do not replay',/** 验证采集后台观察、状态恢复和通知投递在该用例中的具体边界，完成准备、调用和结果断言。用例：host reload restores an unfinished wait; consumed notices do not replay。 */ async()=>{
  const entered=Promise.withResolvers();
  const h=harness({/** 提供该用例定义的可控采集服务响应，模拟等待、终态、取消或短暂不可用。 */ read:(_route,_args,signal)=>new Promise((resolve,reject)=>{
    entered.resolve();signal.addEventListener('abort',()=>reject(signal.reason),{once:true});
  })});h.plan();h.capture();await h.runtime.reconcile(h.agent);await entered.promise;
  await h.dispose();
  const next=harness({data:h.data,events:h.events});next.runtime.restore();await next.runtime.settled();
  assert.equal(next.sent.length,1);next.consume();await next.dispose();
  const restored=harness({data:next.data,events:next.events});restored.runtime.restore();await restored.runtime.settled();
  assert.equal(restored.sent.length,0);assert.equal(restored.requests.length,0);await restored.dispose();
});

test('failed repair notification identifies the preserved baseline across runs',async()=>{
  const observation=failure();
  const base={task_id:'sf_previous.scene0',revision:2,token:'a'};
  observation.tasks[0].failure.scene_review.repair_progress={decision:'rollback',round:4,
    next_base:base,baseline:base,candidate:{task_id:'sf_scene.scene0',revision:0,token:'b'},findings:[]};
  const h=harness({read:async()=>observation});
  h.plan();h.capture();await h.runtime.reconcile(h.agent);await h.runtime.settled();
  assert.match(h.sent[0].content[0].text,/"next_base":\{"task_id":"sf_previous.scene0","revision":2/);
  await h.dispose();
});

test('failed scene media comes from scoped evidence and retains the exact before/after revisions',async()=>{
  const root=await fs.mkdtemp(path.join(os.tmpdir(),'scene-repair-evidence-'));
  try {
    const image=path.join(root,'view_4.png');
    await fs.writeFile(image,Buffer.from('89504e470d0a1a0a','hex'));
    const base={task_id:'sf_base.scene0',revision:2,token:'base'};
    const progress={baseline:base,next_base:base,decision:'rollback',round:4,
      findings:[{kind:'regressed',after_view:'view_4.png',before_view:'view_1.png'}]};
    const task={id:'sf_current.scene0',revision:0,failure:{scene_review:{passed:false,repair_progress:progress}}};
    const evidence=repairEvidence(task);
    assert.deepEqual(evidence.program,{task_id:base.task_id,revision:2,file:'program.json'});
    const scope={session_id:'owner'};
    const ctx={llm:{resolveModelInfo:async()=>({inputModalities:['image']})},
      attachments:{saveImage:async({data})=>({size:data.length})}};
    const agent={ctx:{get:key=>ctx[key]},session:{requestHeader:()=>({config:{model:'vision',provider:'local'}})},options:{}};
    const requests=[];
    const blocks=await repairImageContent([task],agent,new AbortController().signal,{
      access:{scope:()=>scope},service:async(route,args,signal,options)=>{
        assert.equal(route,'/evidence');assert.equal(options.scope,scope);requests.push(args);return {image_path:image};
      }});
    assert.deepEqual(requests.map(x=>x.file),['capture/view_4.png','capture/view_1.png']);
    assert.deepEqual(requests.map(x=>x.revision),[0,2]);
    assert.deepEqual(blocks.map(x=>x.type),['text','image','text','image']);
    assert.equal(repairEvidence({state:'SUCCEEDED'}),undefined);
  } finally {await fs.rm(root,{recursive:true,force:true});}
});

test('consecutive failures are compared per scene and by the retained evidence, not reviewer wording',()=>{
  const base={task_id:'sf_origin.scene0',revision:0,token:'frozen'};
  const sample=(round,token,description)=>({tasks:[{
    failure:{scene_review:{repair_progress:{decision:'rollback',round,next_base:{...base,token}}}},
    diagnostics:[{issues:[description]}]}]});
  assert.equal(repeatedRepairFailure(sample(3,'a','placement'),sample(4,'a','contact')),true);
  assert.equal(repeatedRepairFailure(sample(3,'a','placement'),sample(4,'b','contact')),false);
  assert.equal(repeatedRepairFailure(sample(3,'a','placement'),sample(5,'a','contact')),false);
});

test('a transport failure offers only the original failed task for retry, without repair feedback',async()=>{
  const terminal=Promise.withResolvers();
  const h=harness({read:()=>terminal.promise});h.plan();h.capture('sf_original');
  await h.runtime.reconcile(h.agent);
  assert.equal(h.runtime.waitingForCapture(h.agent),true);
  terminal.resolve({run_id:'sf_original',state:'FAILED_FINAL',terminal:true,tasks:[
    {id:'sf_original.scene0',state:'FAILED_FINAL',phase:'capture',failure:{stage:'capture_execution',error:'OperationalError'}},
  ]});
  await h.runtime.settled();
  const message=h.sent[0].content[0].text;
  assert.match(message,/"recovery":\{"tool":"benchforge_retry","arguments":\{"task_id":"sf_original.scene0"\}\}/);
  assert.doesNotMatch(message,/本次采集验收失败|批量预检/);
  assert.equal(h.runtime.waitingForCapture(h.agent),false);
  await h.dispose();
});

test('a review deadline in a turn hook preserves the turn and the original capture',async()=>{
  let offline=true;
  const h=harness({review:()=>{if(offline)throw new DOMException('review deadline','TimeoutError');return null;}});
  h.plan();h.capture();
  await h.runtime.reconcile(h.agent);
  await h.stop();
  assert.equal(h.sent.length,0);
  offline=false;
  await h.runtime.reconcile(h.agent);await h.runtime.settled();
  assert.equal(h.sent.length,1);
  await h.dispose();
});

test('a background review outage keeps the same observer and honors a newly paused review',async()=>{
  let reads=0;
  const h=harness({review:()=>{
    if(++reads===1)throw Object.assign(new Error('review unavailable'),{code:'BENCHFORGE_REVIEW_STORAGE_UNAVAILABLE'});
    return {state:'paused'};
  }});
  await h.runtime.register(h.agent,{run_id:'sf_scene'});
  await h.runtime.settled();
  assert.equal(reads,2);assert.equal(h.sent.length,0);assert.equal(h.requests.length,0);
  assert.equal(h.runtime.records(h.agent.id)[0].state,'paused');await h.dispose();
});

test('a notice lost before log commit is redelivered after restart',/** 验证采集后台观察、状态恢复和通知投递在该用例中的具体边界，完成准备、调用和结果断言。用例：a notice lost before log commit is redelivered after restart。 */ async()=>{
  const h=harness();h.plan();h.capture();await h.runtime.reconcile(h.agent);await h.runtime.settled();await h.dispose();
  const next=harness({data:h.data,events:h.events});next.runtime.restore();await next.runtime.settled();
  assert.equal(next.sent.length,1);await next.dispose();
});

test('reload during an unfinished repair resumes once after the original notice was consumed',/** 验证采集后台观察、状态恢复和通知投递在该用例中的具体边界，完成准备、调用和结果断言。用例：reload during an unfinished repair resumes once after the original notice was consumed。 */ async()=>{
  const h=harness();h.plan();h.capture();await h.runtime.reconcile(h.agent);await h.runtime.settled();h.consume();
  h.append('turn/end',{turn:1,reason:{kind:'aborted',reason:{kind:'disposed'}}});await h.dispose();
  const next=harness({data:h.data,events:h.events});next.runtime.restore();await next.runtime.settled();
  assert.equal(next.sent.length,1);assert.match(next.sent[0].source.noticeKey,/:disposal:/);
  assert.equal(next.requests.length,0);next.consume();await next.dispose();
  const again=harness({data:next.data,events:next.events});again.runtime.restore();await again.runtime.settled();
  assert.equal(again.sent.length,0);await again.dispose();
});

test('explicit cancellation and a paused review park results until authorized continuation',/** 验证采集后台观察、状态恢复和通知投递在该用例中的具体边界，完成准备、调用和结果断言。用例：explicit cancellation and a paused review park results until authorized continuation。 */ async()=>{
  let state='paused';
  const h=harness({/** 提供该用例的可控复核状态，并允许在读取边界制造并发变化。 */ review:()=>({state})});h.plan();h.capture();await h.runtime.reconcile(h.agent);await h.runtime.settled();
  assert.equal(h.sent.length,0);assert.equal(h.runtime.records(h.agent.id)[0].state,'paused');
  state='elapsed';h.append('turn/start',{turn:2});h.append('user/message',{source:{kind:'benchforge/production-resume'}});
  await h.runtime.reconcile(h.agent);await h.runtime.settled();assert.equal(h.sent.length,1);await h.dispose();
  const cancelled=harness();cancelled.plan();cancelled.capture();
  cancelled.append('turn/end',{turn:1,reason:{kind:'aborted',reason:{kind:'user'}}});
  await cancelled.runtime.reconcile(cancelled.agent);assert.equal(cancelled.requests.length,0);await cancelled.dispose();
});

test('final text does not discharge repair obligations; duplicate queued reminders are suppressed',/** 验证采集后台观察、状态恢复和通知投递在该用例中的具体边界，完成准备、调用和结果断言。用例：final text does not discharge repair obligations; duplicate queued reminders are suppressed。 */ async()=>{
  const h=harness();h.plan();h.capture();await h.runtime.reconcile(h.agent);await h.runtime.settled();h.consume();
  h.append('assistant/message',{message:{content:[{type:'text',text:'下一步我会修复。'}]}});
  await h.stop();await h.stop();assert.equal(h.sent.length,2);
  h.consume();await h.stop();h.consume();await h.stop();
  assert.equal(h.runtime.records(h.agent.id)[0].state,'blocked');
  assert.match(h.sent.at(-1).content[0].text,/连续三次收尾/);
  h.consume();const count=h.sent.length;await h.stop();assert.equal(h.sent.length,count);
  await h.dispose();
});

test('pipeline completion releases capture obligations; unrelated follow-up is not restarted',/** 验证采集后台观察、状态恢复和通知投递在该用例中的具体边界，完成准备、调用和结果断言。用例：pipeline completion releases capture obligations; unrelated follow-up is not restarted。 */ async()=>{
  const h=harness();h.plan();h.capture();await h.runtime.reconcile(h.agent);await h.runtime.settled();h.consume();
  h.call('benchforge_pipeline_run',{state:'completed'});await h.stop();assert.equal(h.sent.length,1);
  h.append('turn/start',{turn:2});h.append('assistant/message',{message:{content:[{type:'text',text:'解释工作流程。'}]}});
  await h.stop();assert.equal(h.sent.length,1);await h.dispose();
});

test('shell activity cannot reset repeated final responses, including after host reload',/** 验证采集后台观察、状态恢复和通知投递在该用例中的具体边界，完成准备、调用和结果断言。用例：shell activity cannot reset repeated final responses, including after host reload。 */ async()=>{
  const h=harness();h.plan();h.capture();await h.runtime.reconcile(h.agent);await h.runtime.settled();h.consume();
  await h.stop();h.consume();h.call('bash',{output:'go'});await h.stop();h.consume();await h.dispose();
  const next=harness({data:h.data,events:h.events});
  next.call('bash',{output:'true'});await next.stop();
  assert.equal(next.runtime.records(next.agent.id)[0].state,'blocked');
  assert.ok(next.sent.every(message=>message.content[0].text.length<1000));
  await next.dispose();
});

test('a newly submitted repair gets its own budget; active captures are never stopped',/** 验证采集后台观察、状态恢复和通知投递在该用例中的具体边界，完成准备、调用和结果断言。用例：a newly submitted repair gets its own budget; active captures are never stopped。 */ async()=>{
  const h=harness();h.plan();h.capture();await h.runtime.reconcile(h.agent);await h.runtime.settled();h.consume();
  await h.stop();h.consume();await h.stop();h.consume();
  h.capture('sf_repaired');await h.runtime.reconcile(h.agent);await h.runtime.settled();h.consume();
  await h.stop();assert.notEqual(h.runtime.records(h.agent.id).at(-1).state,'blocked');
  await h.dispose();
});

test('capture notices deduplicate and bound diagnostics while retaining evidence references',/** 验证采集后台观察、状态恢复和通知投递在该用例中的具体边界，完成准备、调用和结果断言。用例：capture notices deduplicate and bound diagnostics while retaining evidence references。 */ ()=>{
  const issues=Array.from({length:30},(_,i)=>`object_${i}: wrong support `+'x'.repeat(800));
  const records={scene_review_path:'/artifacts/review.json'};
  const value=captureNotice({run_id:'sf_scene',state:'FAILED_FINAL',tasks:[{id:'sf_scene.scene0',records,
    failure:{scene_review:{issues}},quality:{feedback:{issues}},diagnostics:[{issues}]}]});
  assert.equal(value.issue_count,30);assert.equal(value.issues.length,12);
  assert.ok(JSON.stringify(value).length<8000);assert.deepEqual(value.tasks[0].records,records);
  assert.deepEqual(value.detail,{tool:'benchforge_scene_status',run_id:'sf_scene',detail:true});
});

test('retrying a settled service task tracks its new attempt and receives one new result',/** 验证采集后台观察、状态恢复和通知投递在该用例中的具体边界，完成准备、调用和结果断言。用例：retrying a settled service task tracks its new attempt and receives one new result。 */ async()=>{
  const h=harness();await h.runtime.register(h.agent,{run_id:'sf_scene',call_id:'first'});await h.runtime.settled();h.consume();
  await h.runtime.register(h.agent,{run_id:'sf_scene',call_id:'retry',retry:true});await h.runtime.settled();
  assert.equal(h.sent.length,2);assert.notEqual(h.sent[0].source.noticeKey,h.sent[1].source.noticeKey);await h.dispose();
});

test('a retry supersedes an active observation without losing the new generation',/** 验证采集后台观察、状态恢复和通知投递在该用例中的具体边界，完成准备、调用和结果断言。用例：a retry supersedes an active observation without losing the new generation。 */ async()=>{
  const entered=Promise.withResolvers();let reads=0,aborted=false;
  const h=harness({/** 提供该用例定义的可控采集服务响应，模拟等待、终态、取消或短暂不可用。 */ read:async(_route,_args,signal)=>{
    if(++reads>1)return failure();
    entered.resolve();
    return new Promise((resolve,reject)=>signal.addEventListener('abort',()=>{aborted=true;reject(signal.reason);},{once:true}));
  }});
  await h.runtime.register(h.agent,{run_id:'sf_scene',call_id:'first'});await entered.promise;
  await h.runtime.register(h.agent,{run_id:'sf_scene',call_id:'retry',retry:true});await h.runtime.settled();
  assert.equal(aborted,true);assert.equal(reads,2);assert.equal(h.sent.length,1);
  assert.match(h.sent[0].source.noticeKey,/:retry:FAILED_FINAL$/);await h.dispose();
});

test('observer errors are bounded; explicit recovery can deliver a distinct terminal result',/** 验证采集后台观察、状态恢复和通知投递在该用例中的具体边界，完成准备、调用和结果断言。用例：observer errors are bounded; explicit recovery can deliver a distinct terminal result。 */ async()=>{
  let available=false;
  const h=harness({/** 提供该用例定义的可控采集服务响应，模拟等待、终态、取消或短暂不可用。 */ read:async()=>{if(!available)throw Error('service temporarily unavailable');return failure();}});
  h.plan();h.capture();await h.runtime.reconcile(h.agent);await h.runtime.settled();
  assert.equal(h.requests.length,3);assert.equal(h.sent.length,1);h.consume();
  for(let i=0;i<4;i++)await h.runtime.reconcile(h.agent);
  await h.runtime.settled();assert.equal(h.requests.length,3);
  available=true;await h.runtime.register(h.agent,{run_id:'sf_scene'},{resume:true});await h.runtime.settled();
  assert.equal(h.sent.length,2);assert.equal(h.requests.length,4);
  assert.match(h.sent[0].source.noticeKey,/:observer_failed$/);
  assert.match(h.sent[1].source.noticeKey,/:FAILED_FINAL$/);await h.dispose();
});

test('new production objectives do not inherit or wake old capture obligations',/** 验证采集后台观察、状态恢复和通知投递在该用例中的具体边界，完成准备、调用和结果断言。用例：new production objectives do not inherit or wake old capture obligations。 */ async()=>{
  const terminal=Promise.withResolvers(),entered=Promise.withResolvers();
  const h=harness({/** 提供该用例定义的可控采集服务响应，模拟等待、终态、取消或短暂不可用。 */ read:async()=>{entered.resolve();return terminal.promise;}});
  h.plan();h.capture();await h.runtime.reconcile(h.agent);await entered.promise;
  h.call('benchforge_pipeline_plan',{production_contract:'benchforge.production-stages/v1',state:'planned',
    plan:'/workspace/new-plan.json',review_revision:{kind:'new_task'}});
  terminal.resolve(failure());await h.runtime.settled();await h.stop();
  assert.equal(h.sent.length,0);assert.equal(h.runtime.records(h.agent.id)[0].state,'settled');
  assert.equal(captureContinuations(h.events).captures.length,0);await h.dispose();
});

test('execution repairs retain ownership but a completed task is not resurrected on reload',/** 验证采集后台观察、状态恢复和通知投递在该用例中的具体边界，完成准备、调用和结果断言。用例：execution repairs retain ownership but a completed task is not resurrected on reload。 */ async()=>{
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

test('checking an old stage does not complete production after a successful capture',/** 验证采集后台观察、状态恢复和通知投递在该用例中的具体边界，完成准备、调用和结果断言。用例：checking an old stage does not complete production after a successful capture。 */ async()=>{
  const h=harness({/** 提供该用例定义的可控采集服务响应，模拟等待、终态、取消或短暂不可用。 */ read:async()=>({run_id:'sf_scene',state:'SUCCEEDED',terminal:true,tasks:[]})});
  h.plan();h.capture();await h.runtime.reconcile(h.agent);await h.runtime.settled();h.consume();
  h.call('benchforge_stage_review',{stage:'S1',state:'elapsed'});await h.stop();
  assert.equal(h.sent.length,2);h.consume();
  h.call('benchforge_pipeline_run',{state:'waiting_user',stage:'S2'});await h.stop();
  assert.equal(h.sent.length,2);await h.dispose();
});

test('a service submission retains its identity if durable tracking cannot be registered',/** 验证采集后台观察、状态恢复和通知投递在该用例中的具体边界，完成准备、调用和结果断言。用例：a service submission retains its identity if durable tracking cannot be registered。 */ async()=>{
  const exec={agent:{id:'owner'},callId:'capture-call'};
  const tracking={/** 模拟持久化登记失败，验证已提交的采集回执仍可返回和恢复。 */ register:async()=>{throw Error('storage write failed');}};
  const submitted=await trackCaptureSubmission({state:'accepted',run_id:'sf_scene',task_id:'sf_scene.scene0'},'/capture',{},exec,tracking);
  assert.equal(submitted.run_id,'sf_scene');assert.equal(submitted.state,'submitted');
  assert.equal(submitted.submission_accepted,true);assert.equal(submitted.scene_acceptance,'pending');
  assert.equal(submitted.watch.accepted,false);assert.match(submitted.next,/do not submit a duplicate/);
  const retried=await trackCaptureSubmission({task_id:'sf_scene.scene0'},'/retry',{},exec,tracking);
  assert.equal(retried.run_id,'sf_scene');assert.equal(Object.hasOwn(retried,'state'),false);
  assert.deepEqual(JSON.parse(JSON.stringify(retried)),retried);
});

test('quality failure cannot masquerade as an external blocker; unchanged evidence can stop repeated repairs',/** 验证采集后台观察、状态恢复和通知投递在该用例中的具体边界，完成准备、调用和结果断言。用例：quality failure cannot masquerade as an external blocker; unchanged evidence can stop repeated repairs。 */ async()=>{
  const folder=await fs.mkdtemp(path.join(os.tmpdir(),'benchforge-continuation-'));
  const h=harness({artifacts:folder,/** 提供该用例定义的可控采集服务响应，模拟等待、终态、取消或短暂不可用。 */ read:async(_route,args)=>failure(args.run_id)});
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

test('canonical metadata, PTC and incremental history preserve native ownership',/** 验证采集后台观察、状态恢复和通知投递在该用例中的具体边界，完成准备、调用和结果断言。用例：canonical metadata, PTC and incremental history preserve native ownership。 */ ()=>{
  const h=harness();h.plan();
  h.append('tool/ptc-dispatch',{name:'benchmarkforge_capture',subCallId:'inner',arguments:{},isError:false,
    content:[{type:'text',text:JSON.stringify({run_id:'sf_ptc',state:'submitted'})}]});
  const history=createCaptureHistory();const first=history(h.agent.session);
  assert.equal(first.captures.at(-1).run_id,'sf_ptc');assert.equal(history(h.agent.session),first);
  h.call('benchforge_retry',{task_id:'sf_ptc.scene0',state:'PENDING'});
  assert.equal(history(h.agent.session).captures.at(-1).retry,true);
  assert.deepEqual(history(h.agent.session),captureContinuations(h.events));
});
