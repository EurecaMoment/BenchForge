import fs from 'node:fs/promises';
import path from 'node:path';
import {setTimeout as sleep} from 'node:timers/promises';
import {compactCaptureObservation, captureRunId} from './capture_watch.mjs';
import {createCaptureHistory, captureFailure, failureIdentity, noticeDelivered, terminalCapture} from './continuation_state.mjs';
import {captureNotice} from './notice.mjs';

/** 组合会话所有者与采集运行标识，生成持久化续接记录键。 */
const keyOf = (owner, run) => `${owner}:${run}`;
/** 判断复核是否处于需要暂停自动采集推进的状态。 */
const suspendedReview = review => review && ['paused','needs_input','check_failed'].includes(review.state);
/** 判断持久化续接记录是否仍需要后台观察或最终结果投递。 */
const observable = record => ['watching','terminal','observer_failed','delivered'].includes(record?.state);
/** 把采集终态和诊断转换为用于通知的结果类别。 */
const outcome = record => record.error ? 'observer_failed' : record.observation?.state || record.state;
/** 根据运行、代际和任务上下文生成稳定通知键，避免旧结果重复续接新任务。 */
const noticeKey = (record,context) => `capture:${record.owner}:${record.run_id}:${record.generation}:${outcome(record)}` +
  (context.activeTurn && context.disposalSeq ? `:disposal:${context.disposalSeq}` : '');
/** 将结构化采集结果序列化为通知所需的文本内容。 */
const text = record => {
  const state = outcome(record);
  return `采集结果通知：${state}。${captureFailure(record.observation) ?
    '本次采集验收失败，整个任务仍未完成。先读取完整审查文件，汇总独立的物理与视觉缺项，保留原始硬要求及可辨识资产，按关联成组修复完整程序，经批量预检后重新采集。下面的通知只是摘要。不要通过删除部件、机器人资产或改变用途消除报错。' :
    record.error ? '后台查询失败，保留原作业，先用 benchforge_wait(background=true) 恢复跟踪。不要重复提交仍在运行的作业。' :
    '提交受理、采集完成和场景验收是不同状态。读取实际回执，验收通过后续跑原评测；单次采集结束不等于整项任务交付。'}
${JSON.stringify(captureNotice({...record.observation,run_id:record.run_id},record.error))}
如果真实外部条件阻止修复，使用 benchforge_scene_blocker 保存具体证据与缺失条件，然后向用户说明。可执行的下一步必须现在执行，不能只列计划就结束。`;
};

/** Durable host-owned observations. Only the service decides task outcomes. */
/** 创建由宿主持有的持久化采集观察器，协调重载恢复、暂停、去重投递和任务归属。 */
export function createCaptureContinuation({table, artifacts, read, resolveAgent, createMessage,
  readReview = () => null, signal, onError = () => {}, now = Date.now,
  delay = (ms,signal) => sleep(ms,undefined,{signal,ref:false})}) {
  const active = new Map(), registration = new Map(), agents = new Map();
  const history = createCaptureHistory();
  const lifecycle = new AbortController();
  const lifetime = signal ? AbortSignal.any([signal,lifecycle.signal]) : lifecycle.signal;
  let closed = false;
  /** 按拥有者和运行更新续接记录，拒绝旧代际覆盖或意外解除人工暂停。 */
  const save = async (record, {replace = false} = {}) => {
    const key = keyOf(record.owner,record.run_id);
    if (!table.get(key)) {await table.put(key,record); return record;}
    return table.update(key, current => !replace && (current.generation !== record.generation ||
      ['blocked','paused'].includes(current.state) && current.state !== record.state) ? current : record);
  };
  /** 生成后台等待已接管的工具回执，说明宿主将在终态后返回当前会话。 */
  const receipt = record => ({...record.observation,run_id:record.run_id,
    watch:{background:true,accepted:true,state:record.state,
      instruction:'The host owns this wait and restores it after reload. Terminal success or failure returns to this session.'}});
  /** 列出指定会话拥有的全部持久化采集续接记录。 */
  const records = owner => [...table.entries()].map(([,row])=>row).filter(row=>row.owner === owner);
  /** 优先使用已持有的 Agent，否则通过原生控制器恢复记录所有者。 */
  async function ownerOf(record) {
    return agents.get(record.owner) || await resolveAgent(record.owner);
  }
  /** 核对会话未被中断且阶段复核允许推进，防止后台观察绕过暂停。 */
  async function allowed(agent,signal=lifetime) {
    if (!agent || history(agent.session).interrupted) return false;
    const review=await readReview(agent.session.header.cwd, agent.id,now(),signal);
    signal.throwIfAborted();
    return !history(agent.session).interrupted&&!suspendedReview(review);
  }
  /** 仅向仍拥有该生产任务且允许继续的会话投递终态结果，并持久化通知状态。 */
  async function deliver(record,signal=lifetime) {
    if (!record.error && !terminalCapture(record.observation)) return record;
    const agent = await ownerOf(record);
    if (closed || lifetime.aborted) return record;
    let context = agent && history(agent.session);
    if (context?.completed || context?.taskSeq > (record.task_seq || 0)) return save({...record,state:'settled'});
    if (!await allowed(agent,signal)) return save({...record,state:'paused'});
    context=history(agent.session);
    if(context.completed||context.taskSeq>(record.task_seq||0))return save({...record,state:'settled'});
    const latest = table.get(keyOf(record.owner,record.run_id));
    if (closed || latest?.generation !== record.generation || record.error && latest.error !== record.error ||
        ['blocked','paused','settled'].includes(latest?.state)) return latest;
    const key = noticeKey(record,context);
    if (!noticeDelivered(agent,key)) {
      const message = createMessage({source:{kind:'benchforge/capture-watch',noticeKey:key,form:'notice',summary:`采集状态：${outcome(record)}`,
        capture:{run_id:record.run_id,state:outcome(record),failed:captureFailure(record.observation)}},
        content:[{type:'text',text:text(record)}]});
      // steer wakes a driver even if it is crossing the final-response boundary.
      if (agent.status === 'running') agent.steer(message); else agent.followup(message);
    }
    return save({...record,state:'delivered',notice_key:key});
  }
  /** 启动一个采集代际的后台观察；重入时复用观察器，代际变化时取消旧观察后重启。 */
  function start(record, {restart = false} = {}) {
    if (closed || lifetime.aborted || !observable(record)) return;
    const key = keyOf(record.owner,record.run_id);
    const previous = active.get(key);
    if (previous) {
      if (restart || previous.generation !== record.generation) {
        previous.restart = true;
        previous.controller.abort();
      }
      return;
    }
    const controller = new AbortController();
    const requestSignal = AbortSignal.any([lifetime,controller.signal]);
    const observer = {generation:record.generation,controller,restart:false};
    observer.work = Promise.resolve().then(/** 在当前采集代际内循环观察服务；持续核对任务归属、暂停状态和取消信号后投递终态。 */ async () => {
      let current = record, failures = 0;
      const agent = await ownerOf(current), context = agent && history(agent.session);
      if (closed || requestSignal.aborted) return;
      if (context?.completed || context?.taskSeq > (current.task_seq || 0)) {
        await save({...current,state:'settled'});return;
      }
      if (current.state === 'delivered') {
        if (agent && noticeDelivered(agent,noticeKey(current,context))) return;
      }
      if (!await allowed(agent,requestSignal)) {await save({...current,state:'paused'});return;}
      const updated=history(agent.session);
      if(updated.completed||updated.taskSeq>(current.task_seq||0)){await save({...current,state:'settled'});return;}
      if (current.error || terminalCapture(current.observation)) {await deliver(current,requestSignal);return;}
      while (!closed && !requestSignal.aborted) {
        const stored = table.get(key);
        if (stored?.generation !== current.generation || !observable(stored)) return;
        try {
          const raw = await read(current.observation && !terminalCapture(current.observation) ? '/wait' : '/observe',
            {run_id:current.run_id,timeout_seconds:60},requestSignal);
          if (closed || requestSignal.aborted) return;
          if (raw?.run_id !== current.run_id || !Array.isArray(raw.tasks)) throw Error('Invalid capture observation identity');
          const latest = table.get(key);
          if (latest?.generation !== current.generation || ['blocked','paused'].includes(latest?.state)) return;
          const sceneIds = (raw.tasks || []).map(task => task.failure?.scene_review?.production_acceptance?.scene_id ||
            task.result?.scene_review?.production_acceptance?.scene_id || task.unit?.submitted_program?.program?.scene_id).filter(Boolean);
          current = {...current,observation:compactCaptureObservation(raw,{artifacts}),state:'watching',
            scene_id:sceneIds.length ? JSON.stringify([...new Set(sceneIds)].sort()) : current.scene_id || ''};
          delete current.error;
          if (terminalCapture(raw)) {
            current = await save({...current,state:raw.state === 'CANCELED' ? 'cancelled' : 'terminal',finished_at:now()});
            if (current.generation !== observer.generation || !['terminal','cancelled'].includes(current.state)) return;
            if (raw.state !== 'CANCELED') await deliver(current,requestSignal);
            return;
          }
          await save(current);
          failures = 0;
        } catch (error) {
          if (closed || requestSignal.aborted) return;
          if (++failures < 3) {await delay(1000 * failures,requestSignal); continue;}
          current = await save({...current,state:'observer_failed',error:String(error.message || error)});
          if (current.generation !== observer.generation || current.state !== 'observer_failed') return;
          await deliver(current,requestSignal); return;
        }
      }
    }).catch(error=>{if(!closed&&!requestSignal.aborted)onError(error);}).finally(()=>{
      active.delete(key);
      if (observer.restart) start(table.get(key));
    });
    active.set(key,observer);
  }
  /** 串行登记当前会话的采集提交，保留幂等关系并按需恢复已暂停观察。 */
  async function register(agent, submission, {resume = false} = {}) {
    if (closed) throw Error('Capture tracking is unloading; the submitted task is retained by the service');
    const run = captureRunId(submission.run_id), owner = agent.id;
    if (!/^[A-Za-z0-9_-]+$/.test(owner) || !/^sf_[A-Za-z0-9_-]+$/.test(run)) throw Error('Capture tracking requires a native session and run identity');
    agents.set(owner,agent);
    const key = keyOf(owner,run), previous = registration.get(key) || Promise.resolve();
    const operation = previous.catch(()=>{}).then(/** 在同一会话运行队列内建立或升级续接记录，再启动对应代际的宿主观察器。 */ async()=>{
      let record = table.get(key);
      if (!record) record = await save({owner,run_id:run,generation:submission.call_id || run,
        submitted_at:submission.submitted_at || now(),task_seq:submission.task_seq ?? history(agent.session).taskSeq,state:'watching'});
      if (submission.retry && submission.call_id && record.generation !== submission.call_id) {
        record = await save({owner,run_id:run,generation:submission.call_id,
          submitted_at:submission.submitted_at || now(),task_seq:submission.task_seq ?? history(agent.session).taskSeq,state:'watching'}, {replace:true});
      }
      if (resume && (['paused','observer_failed'].includes(record.state) || record.error)) {
        const {error,notice_key,...retained} = record;
        record = await save({...retained,state:'watching'}, {replace:true});
      }
      if (record.state === 'delivered' && !noticeDelivered(agent,noticeKey(record,history(agent.session)))) {
        record = await save({...record,state:'terminal'});
      }
      if (['watching','terminal','observer_failed'].includes(record.state)) start(record,{restart:resume});
      return receipt(record);
    });
    registration.set(key,operation);
    try {return await operation;} finally {if(registration.get(key)===operation)registration.delete(key);}
  }
  /** 从最新原生采集事件协调续接记录，只恢复仍属于当前任务的运行。 */
  async function reconcile(agent, {recover = false,signal:turnSignal} = {}) {
    const signal=turnSignal?AbortSignal.any([lifetime,turnSignal]):lifetime;
    signal.throwIfAborted();
    const context = history(agent.session);
    if (context.interrupted || context.completed || recover && !context.production) return;
    const latest = context.captures.at(-1);
    if (!latest) return;
    // Upgrade old production sessions only at their latest capture. Never replay
    // historical failed attempts or acquire an unrelated ordinary conversation.
    const review = await readReview(agent.session.header.cwd,agent.id,now(),signal);
    signal.throwIfAborted();
    const updated=history(agent.session);
    if(updated.interrupted||updated.completed||updated.taskSeq!==context.taskSeq)return;
    if (context.production || latest.turn === context.turn) await register(agent,latest,
      {resume:context.activeTurn && !suspendedReview(review) && table.get(keyOf(agent.id,latest.run_id))?.state === 'paused'});
  }
  /** 会话停止前核对最新采集和复核状态，登记仍需宿主持有的后台等待。 */
  async function stopping({agent,signal:turnSignal}) {
    const signal=turnSignal?AbortSignal.any([lifetime,turnSignal]):lifetime;
    signal.throwIfAborted();
    const context = history(agent.session);
    if (context.interrupted || context.completed || !context.activeTurn) return;
    const latest = context.captures.at(-1);
    if (!latest || latest.turn !== context.turn && !context.production) return;
    const review = await readReview(agent.session.header.cwd,agent.id,now(),signal);
    signal.throwIfAborted();
    const updated=history(agent.session);
    if(updated.interrupted||updated.completed||updated.taskSeq!==context.taskSeq)return;
    if (suspendedReview(review)||review?.state==='waiting_user') return;
    await register(agent,latest);
    const record = table.get(keyOf(agent.id,latest.run_id));
    if (['watching','paused','blocked','cancelled'].includes(record.state)) return;
    // A saved failure still requires repair after a notification has been read.
    // Successful production captures require the pipeline's next stage receipt.
    if (!captureFailure(record.observation) && !record.error && (!context.production || context.workflowSeq > latest.seq)) return;
    // Count unsuccessful final responses, not tool calls. A diagnostic or shell
    // command is allowed during repair, but cannot buy unlimited new retries.
    const progress = `${record.generation}:${context.workflowSeq}`;
    const count = record.continuation?.progress === progress ? record.continuation.attempts : 0;
    const key = `capture-stop:${agent.id}:${latest.run_id}:${progress}:${count}`;
    if ([...(agent.inbox?.nextStep || []),...(agent.inbox?.nextTurn || [])].some(message=>message.source?.noticeKey===key)) return;
    // Do not count the same queued reminder twice before the model reads it.
    if ([...(agent.inbox?.nextStep || []),...(agent.inbox?.nextTurn || [])].some(message=>message.source?.noticeKey===record.continuation?.notice)) return;
    const continuation = {progress,attempts:count+1,notice:key};
    await save({...record,continuation});
    signal.throwIfAborted();
    if (count >= 2) {
      const reason='执行未推进：连续三次收尾仍无新采集提交或后续阶段回执。保留失败证据，停止自动催促；任务尚未完成，可在修复后重新提交。';
      await save({...record,continuation,state:'blocked',blocked_at:now(),blocker:{kind:'execution',reason,event_seq:context.activitySeq}});
      signal.throwIfAborted();
      agent.steer(createMessage({source:{kind:'benchforge/capture-watch',noticeKey:key+':blocked',form:'notice',summary:reason,
        capture:{run_id:record.run_id,state:'blocked',failed:true,blocked:true}},content:[{type:'text',text:
        reason+' 请向用户明确说明任务未完成及真实缺项；不能宣称交付成功。'}]}));
      return;
    }
    agent.steer(createMessage({source:{kind:'benchforge/capture-continuation',noticeKey:key,form:'notice',summary:'采集或生产仍未完成，继续修复。'},content:[{type:'text',text:
      `采集 ${record.run_id} 的结果没有变化。完整缺项见 benchforge_scene_status(run_id="${record.run_id}",detail=true)。`+
      '继续必要的检查与修复，并提交新采集或推进已通过的阶段；不要降低硬要求。若确有阻塞，保存具体证据并说明。重复进度说明和空命令不算推进。'}]}));
  }
  /** 仅允许为本会话已经结束的采集记录具体阻塞条件和证据，避免遗弃仍在运行的任务。 */
  async function block(agent, {run_id,reason,missing_condition,evidence,kind}) {
    const record = table.get(keyOf(agent.id,captureRunId(run_id)));
    if (!record || !terminalCapture(record.observation) || record.observation.state === 'CANCELED') throw Error('Only a settled task owned by this session can record a blocker; active work remains tracked');
    if (![reason,missing_condition].every(value=>typeof value==='string'&&value.trim().length>=12)) throw Error('Describe the specific blocker and the concrete missing condition');
    if (!Array.isArray(evidence) || !evidence.length) throw Error('A blocker requires saved evidence');
    const folder = await fs.realpath(artifacts);
    for (const file of evidence) {
      const resolved = await fs.realpath(file), relative = path.relative(folder,resolved);
      if (relative.startsWith('..') || path.isAbsolute(relative) || !relative.startsWith(record.run_id+'.') || !(await fs.stat(resolved)).isFile())
        throw Error('Blocker evidence must belong to this service capture');
    }
    if (kind === 'no_progress') {
      const identity = failureIdentity(record.observation);
      const previous = records(agent.id).filter(row=>row.run_id!==record.run_id && row.submitted_at<record.submitted_at).sort((a,b)=>b.submitted_at-a.submitted_at)[0];
      if (!identity || !record.scene_id || record.scene_id !== previous?.scene_id || identity !== failureIdentity(previous?.observation)) throw Error('Two consecutive captures of this scene with the same actual failure are required for a no-progress blocker');
    } else if (kind === 'external') {
      if (!record.error && !(record.observation.tasks || []).some(task=>task.failure && !task.failure.scene_review))
        throw Error('A repairable scene-quality failure is not evidence of an external blocker; repair it or document consecutive unchanged failures');
    } else throw Error('Blocker kind must be external or no_progress');
    await save({...record,state:'blocked',blocker:{kind,reason,missing_condition,evidence},blocked_at:now()});
    return {run_id:record.run_id,state:'blocked',reason,missing_condition,evidence,accepted:false};
  }
  /** 宿主重载后为所有仍可观察的持久化记录重新启动后台观察。 */
  function restore() {
    for (const [,record] of table.entries()) if (observable(record)) start(record);
  }
  /** 重新绑定已恢复的 Agent，并启动其尚未结算的采集观察。 */
  async function resume(agent) {
    agents.set(agent.id,agent);
    for (const record of records(agent.id)) {
      if (observable(record)) start(record);
    }
  }
  /** 持续等待正在登记与观察的采集操作全部结算。 */
  async function settled() {
    while (registration.size || active.size)
      await Promise.allSettled([...registration.values(),...[...active.values()].map(observer=>observer.work)]);
  }
  /** 禁止新增观察、取消整个续接寿命，并等待已持有任务退出。 */
  async function dispose() {closed = true;lifecycle.abort();await settled();}
  return {register,reconcile,stopping,block,restore,resume,dispose,settled,records};
}
