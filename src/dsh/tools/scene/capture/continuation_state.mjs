import {canonicalToolName} from '../../invocation/identifiers.mjs';
import {budgetRejection, captureProducers} from './budget.mjs';
import {backgroundWaitRun} from './capture_watch.mjs';
/** 合并消息中的文本块，供识别生产请求与通知内容使用。 */
const body = message => (message?.content || []).filter(block => block.type === 'text').map(block => block.text).join('\n');
/** 容错解析工具事件中的 JSON 文本，忽略无法解析的内容。 */
const json = text => {try {return JSON.parse(text);} catch {return null;}};

/** 创建空的采集历史归约状态，初始化轮次、调用、任务代际和中断标记。 */
const initial = () => ({calls:new Map(),captures:new Map(),budgets:new Map(),waits:new Set(),turn:0,production:false,completed:false,
  interrupted:false,disposalSeq:0,workflowSeq:0,activitySeq:0,activeTurn:false,taskSeq:0,taskKey:'',seq:-1});
/** 按事件序号增量归约原生历史，追踪生产任务、采集提交、停止及交付状态。 */
function consume(state, events) {
  const {calls,captures,budgets,waits} = state;
  for (const event of events) {
    if (event.seq != null && event.seq <= state.seq) continue;
    state.seq = event.seq ?? state.seq;
    const data = event.data;
    if (event.type === 'turn/start') {state.turn = data.turn; state.interrupted = false; state.disposalSeq = 0; state.activeTurn = false;}
    if (event.type === 'user/message' && ['benchforge/capture-watch','benchforge/capture-continuation','benchforge/production-resume'].includes(data.source?.kind)) state.activeTurn = true;
    if (event.type === 'user/message' && data.source?.kind === 'benchforge/capture-watch') waits.delete(data.source.capture?.run_id);
    if (event.type === 'turn/end' && data.reason?.kind === 'aborted') {
      state.interrupted = data.reason.reason?.kind !== 'disposed';
      if (!state.interrupted) state.disposalSeq = event.seq;
    }
    if (event.type === 'tool/call') {calls.set(data.callId, {...data,turn:state.turn}); state.activitySeq = event.seq;}
    if (event.type !== 'tool/result' && event.type !== 'tool/ptc-dispatch') continue;
    const ptc = event.type === 'tool/ptc-dispatch';
    if (ptc) state.activitySeq = event.seq;
    const message = ptc ? {content:data.content,isError:data.isError} : data.message;
    const callId = ptc ? data.subCallId : message?.source?.callId || message?.toolCallId;
    const call = ptc ? {...data,turn:state.turn} : calls.get(callId);
    calls.delete(callId);
    if (!call) continue;
    const name = canonicalToolName(data.meta?.benchforgeInvocation?.resolved?.name || call.name);
    const text = body(message);
    const budget = captureProducers.has(name) && budgetRejection(text);
    if (budget) budgets.set(budget.scene_key || budget.scene_id, {budget,seq:event.seq});
    if (message?.isError || data.error || budget) continue;
    const receipt = json(text);
    if (!receipt) continue;
    if (name === 'benchforge_pipeline_plan' && receipt.production_contract === 'benchforge.production-stages/v1') {
      const key = String(receipt.stage_review?.review_id || receipt.plan || event.seq);
      if ((receipt.review_revision?.kind === 'new_task' || state.completed || !state.production) && key !== state.taskKey) {
        captures.clear();
        budgets.clear();
        waits.clear();
        state.taskSeq = event.seq;
        state.taskKey = key;
        state.workflowSeq = 0;
      }
      state.production = true;
      state.completed = false;
      state.activeTurn = true;
    }
    if (name === 'benchforge_pipeline_run' || name === 'benchforge_stage_review') {
      state.activeTurn = true;
      if (name === 'benchforge_pipeline_run' && ['completed','waiting_user'].includes(receipt.state) ||
          name === 'benchforge_stage_review' && /^S[2-5]$/.test(receipt.stage) && ['waiting_user','elapsed','continued'].includes(receipt.state))
        state.workflowSeq = event.seq;
    }
    if (['benchforge_scene_status','benchforge_scene_evidence','benchforge_wait'].includes(name)) state.activeTurn = true;
    if (name === 'benchforge_wait' && backgroundWaitRun(receipt)) waits.add(receipt.run_id);
    if ((['benchforge_scene_status','benchforge_wait'].includes(name) && terminalCapture(receipt)) ||
        (name === 'benchforge_cancel' && receipt.cancel_requested === true)) waits.delete(receipt.run_id);
    if (receipt.production_contract === 'benchforge.production-stages/v1') state.production = true;
    if (name === 'benchforge_pipeline_run' && receipt.state === 'completed') state.completed = true;
    const run = receipt.run_id || (name === 'benchforge_retry' ? receipt.task_id?.split('.')[0] : '');
    if (!captureProducers.has(name) || !/^sf_[A-Za-z0-9_-]+$/.test(run || '')) continue;
    captures.delete(run);
    captures.set(run, {run_id:run,call_id:callId,seq:event.seq,retry:name === 'benchforge_retry',
      submitted_at:event.time || 0,turn:call.turn,production:state.production,task_seq:state.taskSeq});
    state.activeTurn = true;
  }
  const {calls:ignored,...value} = state;
  return {...value,captures:[...captures.values()],budgets:[...budgets.values()],waits:[...waits]};
}

/** Recover ownership and obligations from standard DSH events, including PTC. */
/** 从完整事件历史构建采集续接上下文。 */
export function captureContinuations(events) {return consume(initial(),events);}

/** Parse each settled tool result once, even in long-running conversations. */
/** 按会话弱引用缓存增量归约状态，历史回退时重建缓存。 */
export function createCaptureHistory() {
  const sessions = new WeakMap();
  return /** 根据会话最新事件序号增量更新历史缓存；事件回退时重新构建归约状态。 */ session => {
    const events = session.snapshotEvents(), last = events.at(-1)?.seq ?? -1;
    let cached = sessions.get(session);
    if (!cached || last < cached.state.seq) {cached = {state:initial()}; sessions.set(session,cached);}
    if (last !== cached.state.seq || !cached.view) cached.view = consume(cached.state,events);
    return cached.view;
  };
}

/** 同时检查待处理收件箱和已持久化历史，判断同一通知是否已经投递。 */
export function noticeDelivered(agent, key) {
  const pending = [...(agent.inbox?.nextStep || []), ...(agent.inbox?.nextTurn || [])];
  return pending.some(message => message.source?.noticeKey === key) ||
    agent.session.snapshotEvents().some(event => event.type === 'user/message' && event.data.source?.noticeKey === key);
}

/** 识别显式终态或成功、最终失败、取消等采集结束状态。 */
export function terminalCapture(value) {
  return value?.terminal === true || ['SUCCEEDED','FAILED_FINAL','CANCELED'].includes(value?.state);
}

/** 综合运行状态、任务失败和场景质量判定采集是否存在实质失败。 */
export function captureFailure(value) {
  return value?.state === 'FAILED_FINAL' || (value?.tasks || []).some(task =>
    task.state === 'FAILED_FINAL' || task.failure?.scene_review?.passed === false ||
    task.result?.scene_review?.passed === false || task.quality?.scene_quality_passed === false);
}

/** 对去重后的失败诊断生成稳定标识，使相同错误不会反复触发新通知。 */
export function failureIdentity(value) {
  const failures = (value?.tasks || []).flatMap(task => task.diagnostics || []).flatMap(row => [
    ...(Array.isArray(row.issues) ? row.issues : []), ...(Array.isArray(row.errors) ? row.errors : []), ...(row.error ? [row.error] : []),
  ]);
  /** 递归排序对象键并保留数组次序，生成与字段插入顺序无关的诊断表示。 */
  const stable = value => value && typeof value === 'object' ? Array.isArray(value) ? value.map(stable) :
    Object.fromEntries(Object.keys(value).sort().map(key => [key,stable(value[key])])) : value;
  return failures.length ? JSON.stringify([...new Set(failures.map(value=>JSON.stringify(stable(value))))].sort()) : '';
}

/** 由服务端对同一基线的两份回执确认未推进，评审的自由文本可以改变措辞。 */
export function repeatedRepairFailure(previous, current) {
  const progress = observation => observation?.tasks?.length === 1 ?
    observation.tasks[0].failure?.scene_review?.repair_progress : undefined;
  const before = progress(previous), after = progress(current);
  const base = after?.next_base, old = before?.next_base;
  const stalled = new Set(['rollback','keep_baseline','uncompared']);
  if (base?.task_id && base.token && Number.isInteger(base.revision) && old?.task_id === base.task_id &&
      old.revision === base.revision && old.token === base.token && stalled.has(before.decision) &&
      stalled.has(after.decision) && Number.isInteger(before.round) && after.round === before.round + 1) return true;
  const same = failureIdentity(previous);
  return Boolean(same && same === failureIdentity(current));
}
