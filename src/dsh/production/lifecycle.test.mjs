import assert from 'node:assert/strict';
import test from 'node:test';
import {createProductionLifecycle} from './lifecycle.mjs';
import {productionProjection} from './projection.mjs';

test('production projection consumes canonical execution metadata after a misspelled call', /** 验证生产阶段状态、复核门禁和任务续接在该用例中的具体边界，完成准备、调用和结果断言。用例：production projection consumes canonical execution metadata after a misspelled call。 */ () => {
  let state = productionProjection.init({cwd: '/workspace'});
  state = productionProjection.apply(state, {type: 'tool/call', seq: 1, time: 1, data: {
    callId: 'c1', name: 'benchmarkforge_pipeline_plan', arguments: '{"workspace":"/wrokspace"}',
  }});
  state = productionProjection.apply(state, {type: 'tool/result', seq: 2, time: 2, data: {
    message: {toolCallId: 'c1', content: [{type: 'text', text: JSON.stringify({state: 'planned', plan: '/workspace/plan.json',
      production_contract: 'benchforge.production-stages/v1'})}]},
    meta: {benchforgeInvocation: {resolved: {name: 'benchforge_pipeline_plan', arguments: {workspace: '/workspace'}}}},
  }});
  assert.equal(state.view.active, true);
  assert.equal(state.view.workspace, '/workspace');
  assert.equal(state.view.lastTool, 'benchforge_pipeline_plan');
  assert.equal(state.view.plan, '/workspace/plan.json');
});

/** 创建可控时钟、事件投影、持久化表与调度队列，驱动生产复核生命周期测试。 */
function harness() {
  let clock=100000,state=productionProjection.init({cwd:'/workspace'}),seq=0,live=true,restored=0;
  const events=[],sent=[],records=new Map(),timers=[];
  const agent={id:'session-test',status:'running',session:{header:{cwd:'/workspace'}},
    inbox:{nextTurn:[],nextStep:[]},/** 记录当前步骤收到的引导消息，并加入测试收件箱。 */ steer:message=>{sent.push(message);agent.inbox.nextStep.push(message);},
    /** 记录下一轮收到的跟进消息，并加入测试收件箱。 */
    followup:message=>{sent.push(message);agent.inbox.nextTurn.push(message);}};
  const table={/** 按测试键读取内存记录或模拟服务。 */ get:id=>records.get(id),/** 保存测试记录的副本，模拟持久化写入并防止引用共享。 */ put:async(id,value)=>{records.set(id,value);},/** 返回测试内存表中的全部记录，供恢复与遍历逻辑使用。 */ entries:()=>records.entries()};
  /** 追加递增序号的原生事件，归约生产投影并立即校验状态结构。 */
  const event=(type,data)=>{const row={seq:++seq,time:clock,type,data};events.push(row);state=productionProjection.apply(state,row);productionProjection.stateSchema.parse(state);return row;};
  let review={id:1,state:'waiting_user',deadline:400,stage:'S1'};
  /** 用测试调度器、可控复核服务和 Agent 恢复计数创建生产生命周期实例。 */
  const create=(advanceReview)=>createProductionLifecycle({projection:{/** 返回夹具当前生产投影，模拟会话投影服务。 */ stateOf:()=>state},table,agents:{/** 按夹具的驻留标记决定当前 Agent 是否可直接取得。 */ get:()=>live?agent:undefined},advanceReview,
    /** 恢复测试 Agent 的驻留状态，并累计宿主恢复次数。 */
    restoreAgent:async()=>{restored++;live=true;return agent;},
    /** 根据可控时钟返回复核等待或已到期状态。 */
    readReview:async()=>{
      if(review instanceof Error)throw review;
      return {...review,state:review.state==='waiting_user'&&clock>=400000?'elapsed':review.state};
    },
    /** 保留测试消息结构，便于断言实际投递内容。 */
    createMessage:value=>value,/** 返回夹具当前毫秒时钟，避免测试依赖真实时间。 */ now:()=>clock,/** 记录待执行定时任务和延迟，供测试按需触发。 */ schedule:(fn,ms)=>{timers.push({fn,ms});return timers.length;},/** 测试调度器的移除接口由用例显式控制，无需真实计时器。 */ unschedule:()=>{}});
  /** 按标准工具调用与结果事件写入业务回执，驱动真实生产投影。 */
  const receipt=(name,value)=>{
    event('tool/call',{name,callId:'call-'+seq,arguments:'{"workspace":"/workspace"}'});
    const callId=events.at(-1).data.callId;
    event('tool/result',{message:{source:{callId},content:[{type:'text',text:JSON.stringify(value)}]}});
  };
  /** 开启生产轮次并写入带 S1 复核门禁的计划回执。 */
  const plan=()=>{
    event('turn/start',{turn:1});
    receipt('benchforge_pipeline_plan',{state:'planned',production_contract:'benchforge.production-stages/v1',plan:'/workspace/plan.json',total_steps:4,
      stage_review:{review_id:1,stage:'S1',artifact:'/workspace/plan.json',deadline:400,state:'waiting_user'}});
  };
  /** 执行生产准入中间件，并收集其追加的提示消息。 */
  const preStep=async runtime=>{
    const result=await runtime.preStep({agent},async()=>({kind:'enter',messages:[]}));
    sent.push(...result.messages);
    return result;
  };
  /** 执行已登记且延迟为零的调度任务，模拟宿主即时协调。 */
  const fire=async()=>{for(const timer of timers.splice(0))if(timer.ms===0)await timer.fn();};
  return {agent,event,receipt,plan,create,sent,records,timers,preStep,fire,events,/** 读取夹具当前供界面消费的生产视图。 */ view:()=>state.view,/** 设置夹具时钟，精确覆盖复核等待边界。 */ setTime:value=>clock=value,/** 替换夹具复核快照，模拟服务端状态变化。 */ setReview:value=>review=value,
    /** 将 Agent 标为非驻留，模拟宿主重载后需要恢复会话。 */
    cold:()=>live=false,/** 返回 Agent 恢复次数，供断言后台协调是否重复恢复。 */ restores:()=>restored};
}

test('review outages preserve the turn and review record, then recover on the next step',async()=>{
  const h=harness(),runtime=h.create();
  h.plan();
  await h.preStep(runtime);
  const before=h.records.get(h.agent.id);
  h.setReview(Object.assign(new Error('backend unavailable'),{code:'BENCHFORGE_REVIEW_STORAGE_UNAVAILABLE'}));
  assert.equal((await h.preStep(runtime)).kind,'enter');
  await runtime.stopping({agent:h.agent,turn:1});
  await runtime.stopping({agent:h.agent,turn:1});
  assert.equal(h.sent.filter(message=>message.source?.noticeKey?.endsWith(':review-unavailable')).length,1);
  assert.equal(h.records.get(h.agent.id),before);
  h.setReview({review_id:1,state:'paused',stage:'S1',deadline:500});
  await h.preStep(runtime);
  assert.equal(h.records.get(h.agent.id).review.state,'paused');
  await runtime.dispose();
});

test('restored production notices adopt migrated review IDs without rewriting session events', /** 验证生产阶段状态、复核门禁和任务续接在该用例中的具体边界，完成准备、调用和结果断言。用例：restored production notices adopt migrated review IDs without rewriting session events。 */ async () => {
  const h = harness(), runtime = h.create();
  h.plan();
  h.setReview({review_id: 42, state: 'waiting_user', deadline: 400, stage: 'S1'});
  await h.preStep(runtime);
  assert.equal(h.records.get('session-test').review.review_id, 42);
  assert.equal(h.records.get('session-test').key, 'session-test:42');
  assert.equal(h.view().review.review_id, 1);
  await runtime.dispose();
});

test('capture submissions and terminal notices project distinct production states without approving scenes',/** 验证生产阶段状态、复核门禁和任务续接在该用例中的具体边界，完成准备、调用和结果断言。用例：capture submissions and terminal notices project distinct production states without approving scenes。 */ ()=>{
  const h=harness();h.plan();
  h.receipt('benchforge_capture',{run_id:'sf_capture',state:'submitted',scene_acceptance:'pending'});
  assert.equal(h.view().state,'waiting_capture');assert.equal(h.view().stage,'S2');assert.equal(h.view().review,null);
  h.event('user/message',{source:{kind:'benchforge/capture-watch',capture:{run_id:'sf_capture',state:'FAILED_FINAL',failed:true}}});
  assert.equal(h.view().state,'needs_repair');assert.equal(h.view().completed,0);
  h.event('user/message',{source:{kind:'benchforge/capture-watch',capture:{run_id:'sf_capture',state:'SUCCEEDED',failed:false}}});
  assert.equal(h.view().state,'scene_ready');assert.equal(h.view().completed,0);
  h.event('user/message',{source:{kind:'benchforge/capture-watch',capture:{run_id:'sf_capture',state:'observer_failed',failed:false}}});
  assert.equal(h.view().state,'capture_error');
  h.receipt('benchforge_scene_blocker',{run_id:'sf_capture',state:'blocked',reason:'Required asset is unavailable.'});
  assert.equal(h.view().state,'blocked');
});

const productionRequest={source:{kind:'user'},content:[{type:'text',text:
  '请实际执行一次 BenchForge 评测数据集生产任务，完整完成五阶段。\nexecution_mode=production；quantity_contract=minimum。\n生产100条机器狗轨迹数据。'}]};
const emptyReply={message:{role:'assistant',content:[]}};

test('production is tracked before its first saved plan and an empty stop requests the next concrete action',/** 验证生产阶段状态、复核门禁和任务续接在该用例中的具体边界，完成准备、调用和结果断言。用例：production is tracked before its first saved plan and an empty stop requests the next concrete action。 */ async()=>{
  const h=harness();h.event('turn/start',{turn:1});h.event('user/message',productionRequest);
  h.event('assistant/message',{message:{content:[{type:'text',text:'先读取合同。'}]}});
  h.receipt('benchforge_pipeline_catalog',{items:[]});h.event('assistant/message',emptyReply);
  const runtime=h.create();await runtime.stopping({agent:h.agent,turn:1});
  assert.equal(h.view().active,true);assert.equal(h.view().state,'planning');assert.equal(h.view().plan,'');
  assert.match(h.sent.at(-1).content[0].text,/benchforge_pipeline_plan/);
  assert.equal(h.sent.at(-1).source.kind,'benchforge/production-continue');await runtime.dispose();
});

test('repeated empty stops fail visibly after bounded recovery, including a host reload',/** 验证生产阶段状态、复核门禁和任务续接在该用例中的具体边界，完成准备、调用和结果断言。用例：repeated empty stops fail visibly after bounded recovery, including a host reload。 */ async()=>{
  const h=harness();h.event('turn/start',{turn:1});h.event('user/message',productionRequest);
  let runtime=h.create();
  for(let attempt=0;attempt<2;attempt++){
    h.event('assistant/message',emptyReply);await runtime.stopping({agent:h.agent,turn:1});
    h.event('user/message',h.sent.at(-1));h.agent.inbox.nextStep=[];
    await runtime.dispose();runtime=h.create();
  }
  h.event('assistant/message',emptyReply);
  await assert.rejects(runtime.stopping({agent:h.agent,turn:1}),{code:'BENCHFORGE_PRODUCTION_INCOMPLETE'});
  h.event('turn/end',{turn:1,reason:{kind:'error'}});assert.equal(h.view().state,'incomplete');
  assert.equal(h.sent.length,2);await runtime.dispose();
});

test('an old completed turn without a production receipt replays as unfinished',/** 验证生产阶段状态、复核门禁和任务续接在该用例中的具体边界，完成准备、调用和结果断言。用例：an old completed turn without a production receipt replays as unfinished。 */ ()=>{
  const h=harness();h.event('turn/start',{turn:1});h.event('user/message',productionRequest);
  h.event('assistant/message',emptyReply);h.event('turn/end',{turn:1,reason:{kind:'completed'}});
  assert.equal(h.view().state,'incomplete');assert.equal(h.view().running,false);assert.equal(h.view().delivered,false);
});

test('the ending failure replaces an older tool error without declaring production complete',/** 验证生产阶段状态、复核门禁和任务续接在该用例中的具体边界，完成准备、调用和结果断言。用例：the ending failure replaces an older tool error without declaring production complete。 */ ()=>{
  const h=harness();h.event('turn/start',{turn:1});h.event('user/message',productionRequest);
  h.event('tool/call',{name:'benchforge_isaac_sim',callId:'failed-call',arguments:'{}'});
  h.event('tool/result',{message:{toolCallId:'failed-call',isError:true,content:[{type:'text',text:'Older capability error'}]}});
  h.event('turn/end',{turn:1,reason:{kind:'error',error:{message:'database is locked',code:'UNKNOWN'}}});
  assert.equal(h.view().error,'database is locked');assert.equal(h.view().state,'incomplete');
  assert.equal(h.view().running,false);assert.equal(h.view().delivered,false);
});

test('system summaries and quoted execution-mode mentions do not start production',/** 验证生产阶段状态、复核门禁和任务续接在该用例中的具体边界，完成准备、调用和结果断言。用例：system summaries and quoted execution-mode mentions do not start production。 */ ()=>{
  const h=harness();h.event('turn/start',{turn:1});
  h.event('user/message',{...productionRequest,source:{kind:'compaction'}});
  h.event('user/message',{source:{kind:'user'},content:[{type:'text',text:'解释 execution_mode=production 是什么意思'}]});
  assert.equal(h.view().active,false);
});

test('a released review cannot permit a premature final reply to replace continuing production',/** 验证生产阶段状态、复核门禁和任务续接在该用例中的具体边界，完成准备、调用和结果断言。用例：a released review cannot permit a premature final reply to replace continuing production。 */ async()=>{
  const h=harness();h.plan();h.setReview({review_id:1,state:'continued',deadline:100,stage:'S1'});
  h.event('assistant/message',{message:{content:[{type:'text',text:'接下来会继续。'}]}});
  const runtime=h.create();await runtime.stopping({agent:h.agent,turn:1});
  assert.match(h.sent.at(-1).content[0].text,/继续执行下一项/);await runtime.dispose();
});

test('a real waiting gate with a visible update can end its turn without skipping the gate',/** 验证生产阶段状态、复核门禁和任务续接在该用例中的具体边界，完成准备、调用和结果断言。用例：a real waiting gate with a visible update can end its turn without skipping the gate。 */ async()=>{
  const h=harness();h.plan();h.event('assistant/message',{message:{content:[{type:'text',text:'计划已保存，正在等待审核窗口。'}]}});
  const runtime=h.create();await runtime.stopping({agent:h.agent,turn:1});
  assert.equal(h.sent.length,0);await runtime.dispose();
});

test('stop interrupts a pre-step queued behind a stuck background review and prevents a late continuation',/** 验证生产阶段状态、复核门禁和任务续接在该用例中的具体边界，完成准备、调用和结果断言。用例：stop interrupts a pre-step queued behind a stuck background review and prevents a late continuation。 */ async()=>{
  const h=harness();h.plan();const controller=new AbortController(),entered=Promise.withResolvers(),backend=Promise.withResolvers();
  let backendSignal;
  const runtime=h.create(async(_workspace,_session,_id,_now,signal)=>{backendSignal=signal;entered.resolve();return backend.promise;});
  await runtime.preStep({agent:h.agent,signal:controller.signal},async()=>({kind:'enter',messages:[]}));
  const refreshing=runtime.refresh(h.agent.id),refreshStopped=assert.rejects(refreshing,{name:'AbortError'});
  await entered.promise;
  const preStep=runtime.preStep({agent:h.agent,signal:controller.signal},async()=>({kind:'enter',messages:[]}));
  const stopped=assert.rejects(preStep,{name:'AbortError'});
  controller.abort(new DOMException('User stopped','AbortError'));
  await Promise.all([stopped,refreshStopped]);assert.equal(backendSignal.aborted,true);
  backend.resolve({state:'continued',review_id:1,deadline:100,stage:'S1'});
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(h.sent.length,0);assert.equal(h.records.get(h.agent.id).suspended,true);
  await runtime.dispose();
});

test('PTC capture dispatches use the same production projection as native tools',/** 验证生产阶段状态、复核门禁和任务续接在该用例中的具体边界，完成准备、调用和结果断言。用例：PTC capture dispatches use the same production projection as native tools。 */ ()=>{
  const {apply,init,stateSchema}=productionProjection;
  let state=init({cwd:'/workspace'});
  state=apply(state,{type:'tool/ptc-dispatch',seq:1,time:1,data:{subCallId:'plan',name:'benchforge_pipeline_plan',arguments:{workspace:'/workspace'},
    content:[{type:'text',text:JSON.stringify({production_contract:'benchforge.production-stages/v1',state:'planned',stage:'S1'})}]}});
  state=apply(state,{type:'tool/ptc-dispatch',seq:2,time:2,data:{subCallId:'capture',name:'benchforge_capture',arguments:{},
    content:[{type:'text',text:JSON.stringify({run_id:'sf_capture',state:'submitted'})}]}});
  stateSchema.parse(state);assert.equal(state.view.state,'waiting_capture');assert.equal(state.view.stage,'S2');
});

test('tool-only work is visible and prompts a concise update after one minute',/** 验证生产阶段状态、复核门禁和任务续接在该用例中的具体边界，完成准备、调用和结果断言。用例：tool-only work is visible and prompts a concise update after one minute。 */ async()=>{
  const h=harness(),runtime=h.create();h.plan();
  h.setTime(161000);h.event('tool/call',{name:'bash',callId:'shell',arguments:'{}'});
  await h.preStep(runtime);
  assert.equal(h.view().shells,1);
  assert.match(h.sent[0].content[0].text,/简短说明/);
  await h.preStep(runtime);
  assert.equal(h.sent.length,1);
  await runtime.dispose();
});

test('a stale projection never describes a released domain review as waiting',/** 验证生产阶段状态、复核门禁和任务续接在该用例中的具体边界，完成准备、调用和结果断言。用例：a stale projection never describes a released domain review as waiting。 */ async()=>{
  for(const state of ['elapsed','continued']) {
    const h=harness(),runtime=h.create();h.plan();h.setTime(161000);
    h.setReview({id:1,state,deadline:400,stage:'S1'});
    await h.preStep(runtime);
    assert.equal(h.view().review.state,'waiting_user');
    const text=h.sent[0].content[0].text;
    assert.match(text,/该审核已放行/);assert.doesNotMatch(text,/审核仍未放行|审核期间不启动/);
    await runtime.dispose();
  }
});

test('an applied execution repair does not enqueue a second continuation for the same window',/** 验证生产阶段状态、复核门禁和任务续接在该用例中的具体边界，完成准备、调用和结果断言。用例：an applied execution repair does not enqueue a second continuation for the same window。 */ async()=>{
  const h=harness();h.plan();const runtime=h.create();await runtime.observe(h.agent);
  h.setReview({id:1,state:'continued',deadline:400,stage:'S1'});h.setTime(400000);
  await runtime.refresh(h.agent.id);
  assert.equal(h.sent.length,1);
  h.event('user/message',h.sent[0]);h.agent.inbox.nextStep=[];
  h.receipt('benchforge_pipeline_plan',{state:'planned',production_contract:'benchforge.production-stages/v1',
    plan:'/workspace/repaired-plan.json',total_steps:4,
    stage_review:{review_id:1,stage:'S1',artifact:'/workspace/repaired-plan.json',deadline:400,state:'continued'}});
  await runtime.observe(h.agent);await h.fire();
  assert.equal(h.sent.length,1);
  assert.equal(h.records.get(h.agent.id).plan,'/workspace/repaired-plan.json');
  await runtime.dispose();
});

test('long tool errors do not become model progress text',/** 验证生产阶段状态、复核门禁和任务续接在该用例中的具体边界，完成准备、调用和结果断言。用例：long tool errors do not become model progress text。 */ ()=>{
  const h=harness();h.plan();const before=h.view().lastTextAt;
  h.setTime(160000);h.receipt('benchforge_scene_status',{state:'SUCCEEDED'});
  assert.equal(h.view().lastTextAt,before);
  assert.equal(h.view().state,'planned');
});

test('review expiry continues the same plan exactly once across reload',/** 验证生产阶段状态、复核门禁和任务续接在该用例中的具体边界，完成准备、调用和结果断言。用例：review expiry continues the same plan exactly once across reload。 */ async()=>{
  const h=harness();h.plan();let runtime=h.create();await runtime.observe(h.agent);await runtime.dispose();
  h.agent.status='idle';h.setTime(400000);runtime=h.create();await runtime.resume(h.agent);await h.fire();await runtime.resume(h.agent);await h.fire();
  assert.equal(h.sent.length,1);assert.match(h.sent[0].content[0].text,/plan=\/workspace\/plan.json/);
  await runtime.dispose();
});

test('an enqueued notice lost before log commit is recovered after reload',/** 验证生产阶段状态、复核门禁和任务续接在该用例中的具体边界，完成准备、调用和结果断言。用例：an enqueued notice lost before log commit is recovered after reload。 */ async()=>{
  const h=harness();h.plan();let runtime=h.create();await runtime.observe(h.agent);
  h.agent.status='idle';h.setTime(400000);await runtime.resume(h.agent);await h.fire();
  assert.equal(h.sent.length,1);await runtime.dispose();
  h.agent.inbox.nextTurn=[];
  runtime=h.create();await runtime.resume(h.agent);await h.fire();
  assert.equal(h.sent.length,2);
  h.event('user/message',h.sent.at(-1));h.agent.inbox.nextTurn=[];
  await runtime.dispose();runtime=h.create();await runtime.resume(h.agent);await h.fire();
  assert.equal(h.sent.length,2);await runtime.dispose();
});

test('paused review and explicit cancellation never trigger continuation',/** 验证生产阶段状态、复核门禁和任务续接在该用例中的具体边界，完成准备、调用和结果断言。用例：paused review and explicit cancellation never trigger continuation。 */ async()=>{
  const h=harness();h.plan();const runtime=h.create();await runtime.observe(h.agent);
  h.setTime(400000);h.setReview({id:1,state:'paused',deadline:400,stage:'S1'});
  await runtime.resume(h.agent);await h.fire();assert.equal(h.sent.length,0);
  h.setReview({id:1,state:'elapsed',deadline:400,stage:'S1'});
  h.event('turn/end',{turn:1,reason:{kind:'aborted',reason:{kind:'user'}}});
  await runtime.refresh(h.agent.id);assert.equal(h.sent.length,0);await runtime.dispose();
});

test('manual continue can fall back when the prior notice has already settled',/** 验证生产阶段状态、复核门禁和任务续接在该用例中的具体边界，完成准备、调用和结果断言。用例：manual continue can fall back when the prior notice has already settled。 */ async()=>{
  const h=harness();h.plan();const runtime=h.create();await runtime.observe(h.agent);
  h.agent.status='idle';h.setTime(400000);await runtime.resume(h.agent);await h.fire();
  h.event('user/message',h.sent[0]);h.agent.inbox.nextTurn=[];
  h.setReview({id:1,state:'continued',deadline:400,stage:'S1'});
  assert.equal(await runtime.refresh(h.agent.id),false);
  assert.equal(h.sent.length,1);await runtime.dispose();
});

test('only an unload interruption is recoverable automatically',/** 验证生产阶段状态、复核门禁和任务续接在该用例中的具体边界，完成准备、调用和结果断言。用例：only an unload interruption is recoverable automatically。 */ async()=>{
  const h=harness();h.plan();let runtime=h.create();await runtime.observe(h.agent);await runtime.dispose();
  h.event('turn/end',{turn:1,reason:{kind:'aborted',reason:{kind:'disposed'}}});
  h.agent.status='idle';h.setTime(401000);runtime=h.create();await runtime.resume(h.agent);await h.fire();
  assert.equal(h.sent.length,1);await runtime.dispose();
});

test('a committed review notice does not suppress recovery after a later disposal',/** 验证生产阶段状态、复核门禁和任务续接在该用例中的具体边界，完成准备、调用和结果断言。用例：a committed review notice does not suppress recovery after a later disposal。 */ async()=>{
  const h=harness();h.plan();let runtime=h.create();await runtime.observe(h.agent);
  h.agent.status='idle';h.setTime(401000);await runtime.resume(h.agent);await h.fire();
  h.event('user/message',h.sent[0]);h.agent.inbox.nextTurn=[];
  h.event('turn/end',{turn:1,reason:{kind:'aborted',reason:{kind:'disposed'}}});
  await runtime.dispose();runtime=h.create();await runtime.resume(h.agent);await h.fire();
  assert.equal(h.sent.length,2);assert.match(h.sent[1].source.noticeKey,/disposal:/);
  h.event('user/message',h.sent[1]);h.agent.inbox.nextTurn=[];
  await runtime.dispose();runtime=h.create();await runtime.resume(h.agent);await h.fire();
  assert.equal(h.sent.length,2);await runtime.dispose();
});

test('a released review and committed notice never override user cancellation',/** 验证生产阶段状态、复核门禁和任务续接在该用例中的具体边界，完成准备、调用和结果断言。用例：a released review and committed notice never override user cancellation。 */ async()=>{
  const h=harness();h.plan();const runtime=h.create();await runtime.observe(h.agent);
  h.setTime(401000);await runtime.resume(h.agent);await h.fire();
  h.event('user/message',h.sent[0]);h.agent.inbox.nextStep=[];
  h.setReview({id:1,state:'continued',deadline:400,stage:'S1'});
  h.event('turn/end',{turn:1,reason:{kind:'aborted',reason:{kind:'user'}}});
  assert.equal(await runtime.refresh(h.agent.id),false);
  assert.equal(h.sent.length,1);await runtime.dispose();
});

test('startup restores only an expired active production session',/** 验证生产阶段状态、复核门禁和任务续接在该用例中的具体边界，完成准备、调用和结果断言。用例：startup restores only an expired active production session。 */ async()=>{
  const h=harness();h.plan();let runtime=h.create();await runtime.observe(h.agent);await runtime.dispose();
  h.cold();h.agent.status='idle';h.setTime(401000);runtime=h.create();runtime.start();await h.fire();
  assert.equal(h.restores(),1);assert.equal(h.sent.length,1);await runtime.dispose();
});

test('a persisted paused review does not activate a cold session',/** 验证生产阶段状态、复核门禁和任务续接在该用例中的具体边界，完成准备、调用和结果断言。用例：a persisted paused review does not activate a cold session。 */ async()=>{
  const h=harness();h.plan();let runtime=h.create();await runtime.observe(h.agent);await runtime.dispose();
  h.cold();h.setTime(401000);h.setReview({id:1,state:'paused',deadline:400,stage:'S1'});
  runtime=h.create();runtime.start();await h.fire();
  assert.equal(h.restores(),0);assert.equal(h.sent.length,0);await runtime.dispose();
});

test('missing delivery gets a bounded stop-time reminder',/** 验证生产阶段状态、复核门禁和任务续接在该用例中的具体边界，完成准备、调用和结果断言。用例：missing delivery gets a bounded stop-time reminder。 */ async()=>{
  const h=harness();h.plan();const runtime=h.create();h.receipt('benchforge_pipeline_run',{state:'completed',run:'/workspace/run'});
  await runtime.stopping({agent:h.agent,turn:1});await runtime.stopping({agent:h.agent,turn:1});
  assert.equal(h.sent.length,1);assert.match(h.sent[0].content[0].text,/present/);
  await runtime.dispose();
});

test('read-only ordinary conversations do not acquire production lifecycle',/** 验证生产阶段状态、复核门禁和任务续接在该用例中的具体边界，完成准备、调用和结果断言。用例：read-only ordinary conversations do not acquire production lifecycle。 */ async()=>{
  const h=harness();h.event('turn/start',{turn:1});h.receipt('benchforge_pipeline_catalog',{items:[]});
  h.setTime(600000);const runtime=h.create();await h.preStep(runtime);
  assert.equal(h.view().active,false);assert.equal(h.sent.length,0);assert.equal(h.records.size,0);await runtime.dispose();
});

test('ordinary follow-up chat does not inherit a completed task reminder',/** 验证生产阶段状态、复核门禁和任务续接在该用例中的具体边界，完成准备、调用和结果断言。用例：ordinary follow-up chat does not inherit a completed task reminder。 */ async()=>{
  const h=harness();h.plan();const runtime=h.create();
  h.receipt('benchforge_pipeline_run',{state:'completed',run:'/workspace/run'});
  h.event('turn/end',{turn:1,reason:{kind:'completed'}});
  h.event('turn/start',{turn:2});h.setTime(600000);
  await h.preStep(runtime);await runtime.stopping({agent:h.agent,turn:2});
  assert.equal(h.sent.length,0);await runtime.dispose();
});

test('a rejected pre-step never changes admission or injects a progress message',/** 验证生产阶段状态、复核门禁和任务续接在该用例中的具体边界，完成准备、调用和结果断言。用例：a rejected pre-step never changes admission or injects a progress message。 */ async()=>{
  const h=harness();h.plan();h.setTime(600000);const runtime=h.create();
  const rejected={kind:'reject',reason:'paused'};
  assert.equal(await runtime.preStep({agent:h.agent},async()=>rejected),rejected);
  assert.equal(h.sent.length,0);assert.equal(h.records.size,0);await runtime.dispose();
});

test('unchanged production state does not rewrite domain storage on every tool',/** 验证生产阶段状态、复核门禁和任务续接在该用例中的具体边界，完成准备、调用和结果断言。用例：unchanged production state does not rewrite domain storage on every tool。 */ async()=>{
  const h=harness();h.plan();const runtime=h.create();await runtime.observe(h.agent);
  const initial=h.records.get(h.agent.id);
  h.event('tool/call',{name:'bash',callId:'shell',arguments:'{}'});await runtime.observe(h.agent);
  assert.equal(h.records.get(h.agent.id),initial);await runtime.dispose();
});

test('progress reminders preserve fresh user input and can recur without flooding',/** 验证生产阶段状态、复核门禁和任务续接在该用例中的具体边界，完成准备、调用和结果断言。用例：progress reminders preserve fresh user input and can recur without flooding。 */ async()=>{
  const h=harness();h.plan();const runtime=h.create(),input={content:[{type:'text',text:'保留原参数'}]};
  h.setTime(161000);
  const result=await runtime.preStep({agent:h.agent},async()=>({kind:'enter',messages:[input]}));
  assert.equal(result.messages[0],input);assert.equal(result.messages.length,2);
  h.setTime(222000);await h.preStep(runtime);assert.equal(h.sent.length,1);
  await runtime.dispose();
});

test('backend rounds advance with the browser closed, retain their deadline after reload, then resume once',/** 验证生产阶段状态、复核门禁和任务续接在该用例中的具体边界，完成准备、调用和结果断言。用例：backend rounds advance with the browser closed, retain their deadline after reload, then resume once。 */ async()=>{
  const h=harness();h.plan();let calls=0;
  /** 依次返回多轮复核窗口及最终放行状态，验证定时推进遵守每轮等待。 */
  const advance=async()=>{
    calls++;
    return calls===1 ? {state:'waiting_user',deadline:220,stage:'S1'} :
      calls===2 ? {state:'waiting_user',deadline:280,stage:'S1'} : {state:'elapsed',deadline:280,stage:'S1'};
  };
  let runtime=h.create(advance);await runtime.observe(h.agent);
  h.agent.status='idle';h.setTime(160000);await runtime.refresh(h.agent.id);
  assert.equal(h.records.get(h.agent.id).review.deadline,220);
  assert.equal(h.sent.length,0);await runtime.dispose();
  runtime=h.create(advance);await runtime.resume(h.agent);
  assert.equal(h.timers.at(-1).ms,60000);
  h.setTime(220000);await runtime.refresh(h.agent.id);
  assert.equal(h.records.get(h.agent.id).review.deadline,280);
  assert.equal(h.sent.length,0);
  h.setTime(280000);await runtime.refresh(h.agent.id);await runtime.refresh(h.agent.id);
  assert.equal(h.sent.length,1);assert.match(h.sent[0].content[0].text,/三轮自检和等待已完成/);
  await runtime.dispose();
});

test('a pending backend check cannot cause a zero-delay timer spin',/** 验证生产阶段状态、复核门禁和任务续接在该用例中的具体边界，完成准备、调用和结果断言。用例：a pending backend check cannot cause a zero-delay timer spin。 */ async()=>{
  const h=harness();h.plan();const runtime=h.create(async()=>({state:'waiting_user',deadline:100,stage:'S1'}));
  await runtime.observe(h.agent);await runtime.refresh(h.agent.id);
  assert.ok(h.timers.at(-1).ms>=1000);assert.equal(h.sent.length,0);
  await runtime.dispose();
});

test('stale tool projections cannot reset a deadline already advanced by the backend',/** 验证生产阶段状态、复核门禁和任务续接在该用例中的具体边界，完成准备、调用和结果断言。用例：stale tool projections cannot reset a deadline already advanced by the backend。 */ async()=>{
  const h=harness();h.plan();const runtime=h.create();
  h.setReview({id:1,state:'waiting_user',deadline:460,stage:'S1'});
  await runtime.observe(h.agent);
  assert.equal(h.view().review.deadline,400);
  assert.equal(h.records.get(h.agent.id).review.deadline,460);
  h.event('tool/call',{name:'read',callId:'draft',arguments:'{}'});
  await runtime.observe(h.agent);
  assert.equal(h.records.get(h.agent.id).review.deadline,460);
  await runtime.dispose();
});

test('failed artifact checks report the issue once without a production-resume notice',/** 验证生产阶段状态、复核门禁和任务续接在该用例中的具体边界，完成准备、调用和结果断言。用例：failed artifact checks report the issue once without a production-resume notice。 */ async()=>{
  const h=harness();h.plan();h.agent.status='idle';
  const runtime=h.create(async()=>({state:'check_failed',deadline:100,stage:'S1',last_check:{checked_at:160},
    check_failure:{error:'Saved collection source changed'}}));
  await runtime.observe(h.agent);await runtime.refresh(h.agent.id);await runtime.refresh(h.agent.id);
  assert.equal(h.sent.length,1);assert.equal(h.sent[0].source.kind,'benchforge/production-check-failed');
  assert.match(h.sent[0].content[0].text,/Saved collection source changed/);
  assert.equal(h.records.get(h.agent.id).suspended,true);
  await runtime.dispose();
});

test('committed release notices refresh the projection and durable observer without advancing stages',/** 验证生产阶段状态、复核门禁和任务续接在该用例中的具体边界，完成准备、调用和结果断言。用例：committed release notices refresh the projection and durable observer without advancing stages。 */ async()=>{
  const h=harness();h.plan();const runtime=h.create();await runtime.observe(h.agent);
  h.setReview({review_id:1,revision:9,state:'elapsed',deadline:400,stage:'S1'});
  await runtime.refresh(h.agent.id);
  assert.equal(h.view().review.state,'waiting_user'); // queued messages are not yet committed
  assert.equal(h.records.get(h.agent.id).review.state,'elapsed');
  const message=h.sent.at(-1);
  assert.equal(message.source.review.revision,9);
  h.event('user/message',message);
  assert.equal(h.view().review.state,'elapsed');assert.equal(h.view().state,'running');
  assert.equal(h.view().stage,'S1');assert.equal(h.view().completed,0);assert.equal(h.view().delivered,false);
  await runtime.dispose();
});

test('fresh domain changes bypass reminder throttling and persist through ordinary session events',/** 验证生产阶段状态、复核门禁和任务续接在该用例中的具体边界，完成准备、调用和结果断言。用例：fresh domain changes bypass reminder throttling and persist through ordinary session events。 */ async()=>{
  const h=harness();h.plan();const runtime=h.create();
  const initial=await h.preStep(runtime);
  h.event('user/message',initial.messages.at(-1));
  h.setReview({review_id:1,revision:8,state:'continued',deadline:400,stage:'S1'});
  const changed=await h.preStep(runtime);
  assert.equal(changed.messages.length,1);
  assert.equal(changed.messages[0].source.review.state,'continued');
  assert.doesNotMatch(changed.messages[0].content[0].text,/状态=waiting_user/);
  h.event('user/message',changed.messages[0]);
  assert.equal(h.view().state,'running');assert.equal(h.view().review.revision,8);
  assert.equal((await h.preStep(runtime)).messages.length,0);
  await runtime.dispose();
});

test('unstructured, obsolete and out-of-order notices cannot release a newer review',/** 验证生产阶段状态、复核门禁和任务续接在该用例中的具体边界，完成准备、调用和结果断言。用例：unstructured, obsolete and out-of-order notices cannot release a newer review。 */ ()=>{
  const h=harness();h.plan();
  h.receipt('benchforge_stage_review',{review_id:2,revision:7,state:'paused',stage:'S2',artifact:'/workspace/scene.json',deadline:400});
  const source={kind:'benchforge/production-resume',noticeKey:'old',previousReviewId:1,
    review:{review_id:1,revision:20,state:'elapsed',stage:'S1',artifact:'/workspace/plan.json',deadline:100}};
  h.event('user/message',{source});
  assert.equal(h.view().state,'paused');assert.equal(h.view().review.review_id,2);
  h.event('user/message',{source:{...source,noticeKey:'stale',previousReviewId:2,
    review:{...source.review,review_id:2,revision:6,stage:'S2'}}});
  assert.equal(h.view().review.state,'paused');assert.equal(h.view().review.revision,7);
  h.event('user/message',{source:{kind:'benchforge/production-continue',noticeKey:'prose'},
    content:[{type:'text',text:'The timer elapsed and all checks passed.'}]});
  assert.equal(h.view().state,'paused');
});

test('continue recovery carries authoritative release and never mistakes a stale pause for a blocker',/** 验证生产阶段状态、复核门禁和任务续接在该用例中的具体边界，完成准备、调用和结果断言。用例：continue recovery carries authoritative release and never mistakes a stale pause for a blocker。 */ async()=>{
  const h=harness();h.plan();
  h.receipt('benchforge_stage_review',{review_id:1,revision:3,state:'paused',stage:'S1',artifact:'/workspace/plan.json',deadline:400});
  h.setReview({review_id:1,revision:4,state:'continued',deadline:400,stage:'S1'});
  h.event('assistant/message',{message:{content:[{type:'text',text:'准备继续。'}]}});
  const runtime=h.create();await runtime.stopping({agent:h.agent,turn:1});
  assert.equal(h.sent.length,1);assert.equal(h.sent[0].source.kind,'benchforge/production-continue');
  assert.match(h.sent[0].content[0].text,/继续执行下一项/);
  h.event('user/message',h.sent[0]);assert.equal(h.view().state,'running');
  await runtime.dispose();
});

test('a successful canonical retry clears its own error but unrelated successes do not',/** 验证生产阶段状态、复核门禁和任务续接在该用例中的具体边界，完成准备、调用和结果断言。用例：a successful canonical retry clears its own error but unrelated successes do not。 */ ()=>{
  const h=harness();h.plan();
  h.event('tool/call',{name:'benchforge_scene_preflight',callId:'relative',arguments:'{"scene_program_path":"tasks/scene.json"}'});
  h.event('tool/result',{message:{toolCallId:'relative',isError:true,content:[{type:'text',text:'Relative path was not found'}]}});
  h.receipt('benchforge_scene_status',{state:'RUNNING'});
  assert.equal(h.view().error,'Relative path was not found');
  h.receipt('benchforge_pipeline_status',{state:'running'});
  assert.equal(h.view().error,'Relative path was not found');
  h.event('tool/call',{name:'benchmarkforge_scene_preflight',callId:'absolute',arguments:'{"scene_program_path":"/workspace/tasks/scene.json"}'});
  h.event('tool/result',{meta:{benchforgeInvocation:{resolved:{name:'benchforge_scene_preflight',arguments:{workspace:'/workspace'}}}},
    message:{toolCallId:'absolute',content:[{type:'text',text:JSON.stringify({schema:'benchforge.scene-diagnostics/v1',status:'blocked',passed:false})}]}});
  assert.equal(h.view().error,'');assert.equal(h.view().state,'planned');assert.equal(h.view().completed,0);
});

test('an earlier concurrent success cannot erase a newer failed invocation',/** 验证生产阶段状态、复核门禁和任务续接在该用例中的具体边界，完成准备、调用和结果断言。用例：an earlier concurrent success cannot erase a newer failed invocation。 */ ()=>{
  const h=harness();h.plan();
  h.event('tool/call',{name:'benchforge_scene_preflight',callId:'older',arguments:'{}'});
  h.event('tool/call',{name:'benchforge_scene_preflight',callId:'newer',arguments:'{}'});
  h.event('tool/result',{message:{toolCallId:'newer',isError:true,content:[{type:'text',text:'Newer input missing'}]}});
  h.event('tool/result',{message:{toolCallId:'older',content:[{type:'text',text:'{"passed":true}'}]}});
  assert.equal(h.view().error,'Newer input missing');
  h.receipt('benchforge_scene_preflight',{passed:true});
  assert.equal(h.view().error,'');
});

test('older tool receipts cannot downgrade a committed review notice or a newer review window',/** 验证生产阶段状态、复核门禁和任务续接在该用例中的具体边界，完成准备、调用和结果断言。用例：older tool receipts cannot downgrade a committed review notice or a newer review window。 */ ()=>{
  const h=harness();h.plan();
  h.event('user/message',{source:{kind:'benchforge/production-resume',noticeKey:'release',previousReviewId:1,
    review:{review_id:1,revision:9,state:'continued',stage:'S1',artifact:'/workspace/plan.json',deadline:400}}});
  for(const name of ['benchforge_stage_review','benchforge_pipeline_run']) {
    const review={review_id:1,revision:8,state:'waiting_user',stage:'S1',artifact:'/workspace/plan.json',deadline:700};
    h.receipt(name,name==='benchforge_stage_review'?review:{state:'waiting_user',stage_review:review});
    assert.equal(h.view().review.revision,9);assert.equal(h.view().state,'running');
  }
  h.receipt('benchforge_stage_review',{review_id:2,revision:1,state:'waiting_user',stage:'S2',artifact:'/workspace/scene.json',deadline:800});
  h.receipt('benchforge_stage_review',{review_id:1,revision:12,state:'continued',stage:'S1',artifact:'/workspace/plan.json',deadline:400});
  assert.equal(h.view().review.review_id,2);assert.equal(h.view().state,'waiting_user');assert.equal(h.view().stage,'S2');
  h.receipt('benchforge_stage_review',{review_id:2,revision:2,state:'check_failed',stage:'S2',artifact:'/workspace/scene.json',deadline:800});
  assert.equal(h.view().review.state,'check_failed');assert.equal(h.view().completed,0);
});
