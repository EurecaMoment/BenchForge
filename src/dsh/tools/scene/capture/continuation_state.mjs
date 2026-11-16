import {canonicalToolName} from '../../invocation/identifiers.mjs';

const producers = new Set(['benchforge_capture','benchforge_refine','benchforge_run','benchforge_extend','benchforge_retry']);
const body = message => (message?.content || []).filter(block => block.type === 'text').map(block => block.text).join('\n');
const json = text => {try {return JSON.parse(text);} catch {return null;}};

const initial = () => ({calls:new Map(),captures:new Map(),turn:0,production:false,completed:false,
  interrupted:false,disposalSeq:0,workflowSeq:0,activitySeq:0,activeTurn:false,taskSeq:0,taskKey:'',seq:-1});
function consume(state, events) {
  const {calls,captures} = state;
  for (const event of events) {
    if (event.seq != null && event.seq <= state.seq) continue;
    state.seq = event.seq ?? state.seq;
    const data = event.data;
    if (event.type === 'turn/start') {state.turn = data.turn; state.interrupted = false; state.disposalSeq = 0; state.activeTurn = false;}
    if (event.type === 'user/message' && ['benchforge/capture-watch','benchforge/capture-continuation','benchforge/production-resume'].includes(data.source?.kind)) state.activeTurn = true;
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
    if (!call || message?.isError || data.error) continue;
    const name = canonicalToolName(data.meta?.benchforgeInvocation?.resolved?.name || call.name);
    const receipt = json(body(message));
    if (!receipt) continue;
    if (name === 'benchforge_pipeline_plan' && receipt.production_contract === 'benchforge.production-stages/v1') {
      const key = String(receipt.stage_review?.review_id || receipt.plan || event.seq);
      if ((receipt.review_revision?.kind === 'new_task' || state.completed || !state.production) && key !== state.taskKey) {
        captures.clear();
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
    if (receipt.production_contract === 'benchforge.production-stages/v1') state.production = true;
    if (name === 'benchforge_pipeline_run' && receipt.state === 'completed') state.completed = true;
    const run = receipt.run_id || (name === 'benchforge_retry' ? receipt.task_id?.split('.')[0] : '');
    if (!producers.has(name) || !/^sf_[A-Za-z0-9_-]+$/.test(run || '')) continue;
    captures.delete(run);
    captures.set(run, {run_id:run,call_id:callId,seq:event.seq,retry:name === 'benchforge_retry',
      submitted_at:event.time || 0,turn:call.turn,production:state.production,task_seq:state.taskSeq});
    state.activeTurn = true;
  }
  const {calls:ignored,...value} = state;
  return {...value,captures:[...captures.values()]};
}

/** Recover ownership and obligations from standard DSH events, including PTC. */
export function captureContinuations(events) {return consume(initial(),events);}

/** Parse each settled tool result once, even in long-running conversations. */
export function createCaptureHistory() {
  const sessions = new WeakMap();
  return session => {
    const events = session.snapshotEvents(), last = events.at(-1)?.seq ?? -1;
    let cached = sessions.get(session);
    if (!cached || last < cached.state.seq) {cached = {state:initial()}; sessions.set(session,cached);}
    if (last !== cached.state.seq || !cached.view) cached.view = consume(cached.state,events);
    return cached.view;
  };
}

export function noticeDelivered(agent, key) {
  const pending = [...(agent.inbox?.nextStep || []), ...(agent.inbox?.nextTurn || [])];
  return pending.some(message => message.source?.noticeKey === key) ||
    agent.session.snapshotEvents().some(event => event.type === 'user/message' && event.data.source?.noticeKey === key);
}

export function terminalCapture(value) {
  return value?.terminal === true || ['SUCCEEDED','FAILED_FINAL','CANCELED'].includes(value?.state);
}

export function captureFailure(value) {
  return value?.state === 'FAILED_FINAL' || (value?.tasks || []).some(task =>
    task.state === 'FAILED_FINAL' || task.failure?.scene_review?.passed === false ||
    task.result?.scene_review?.passed === false || task.quality?.scene_quality_passed === false);
}

export function failureIdentity(value) {
  const failures = (value?.tasks || []).flatMap(task => task.diagnostics || []).flatMap(row => [
    ...(Array.isArray(row.issues) ? row.issues : []), ...(Array.isArray(row.errors) ? row.errors : []), ...(row.error ? [row.error] : []),
  ]);
  const stable = value => value && typeof value === 'object' ? Array.isArray(value) ? value.map(stable) :
    Object.fromEntries(Object.keys(value).sort().map(key => [key,stable(value[key])])) : value;
  return failures.length ? JSON.stringify([...new Set(failures.map(value=>JSON.stringify(stable(value))))].sort()) : '';
}
