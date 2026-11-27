import fs from 'node:fs/promises';
import path from 'node:path';
import {setTimeout as sleep} from 'node:timers/promises';
import {compactCaptureObservation, captureRunId, pendingRunObservation} from './capture_watch.mjs';
import {createCaptureHistory, captureFailure, repeatedRepairFailure, noticeDelivered, terminalCapture} from './continuation_state.mjs';
import {captureNotice} from './notice.mjs';
import {budgetMatches, captureBudgetCode} from './budget.mjs';

/** 组合会话所有者与采集运行标识，生成持久化续接记录键。 */
const keyOf = (owner, run) => `${owner}:${run}`;
/** 判断复核是否处于需要暂停自动采集推进的状态。 */
const suspendedReview = review => review && ['paused','needs_input','check_failed'].includes(review.state);
const reviewUnavailable = Symbol('review temporarily unavailable');
/** 判断持久化续接记录是否仍需要后台观察或最终结果投递。 */
const observable = record => ['watching','terminal','observer_failed','delivered'].includes(record?.state);
/** 把采集终态和诊断转换为用于通知的结果类别。 */
const outcome = record => record.error ? 'observer_failed' : record.observation?.state || record.state;
/** 最后一次已拍的场景仍要正常验收，只有已失败且用完预算才结束自动修图。 */
const exhausted = record => record?.observation?.tasks?.length === 1 && record.observation.tasks.find(task =>
  task.state === 'FAILED_FINAL' && (task.failure?.stage === 'capture_budget' || task.failure?.code === captureBudgetCode ||
   (task.failure?.scene_review?.passed === false && task.capture_budget?.remaining === 0)))?.capture_budget;
const workflowOf = record => record.observation?.tasks?.find(task=>task.workflow)?.workflow || 'scene';
/** 根据运行、代际和任务上下文生成稳定通知键，避免旧结果重复续接新任务。 */
const noticeKey = (record,context) => `capture:${record.owner}:${record.run_id}:${record.generation}:${outcome(record)}` +
  (context.activeTurn && context.disposalSeq ? `:disposal:${context.disposalSeq}` : '');
/** 将结构化采集结果序列化为通知所需的文本内容。 */
const text = record => {
  const state = outcome(record);
  if (workflowOf(record) !== 'scene') {
    const instruction = record.error ? '用 benchforge_wait(background=true) 恢复原作业跟踪。' :
      '读取已保存的实际作业回执和产物，继续依赖步骤；场景验收和阶段审核照常执行。';
    return `后台作业结果通知：${state}。${instruction}\n` +
      JSON.stringify(captureNotice({...record.observation,run_id:record.run_id},record.error));
  }
  const notice=captureNotice({...record.observation,run_id:record.run_id},record.error);
  const recovery=notice.tasks.some(task=>task.recovery);
  return `采集结果通知：${state}。${exhausted(record) ?
    '本场景的正式采集预算已用完。展示现有实拍和已验收结果；若旧图可以按当前任务标准复核，复用原作业。仍影响使用的缺项据实报告，结束本场景的自动重拍。' : recovery ?
    '采集执行中断。对需要恢复的工作项直接使用下方 recovery 中的工具及原样参数。它会在原作业上续接已保存的原生回执。其他正在运行的作业由宿主分别跟踪。' :
    (record.observation?.tasks || []).some(task=>task.failure?.scene_review || task.quality?.scene_quality_passed === false) ?
    '场景尚未通过。保留原始硬要求。先看本通知的实拍，按 repair_evidence 用 benchforge_scene_evidence 读完整报告和起点程序。仅修妨碍原任务的问题，将同一原因的缺陷一并处理。回退时从指定 program 重做，切换失效的资产或摆放方式。先查能复用的已验收场景。每个场景最多十次正式尝试，通过就推进；到限时展示真实结果和具体缺项。' :
    record.error ? '后台查询失败，保留原作业，先用 benchforge_wait(background=true) 恢复跟踪。不要重复提交仍在运行的作业。' :
    captureFailure(record.observation) ? '读取对应工作项的 detail 与失败阶段，按服务回执恢复原任务。' :
    '提交受理、采集完成和场景验收是不同状态。读取实际回执，验收通过后续跑原评测；单次采集结束不等于整项任务交付。'}
${JSON.stringify(notice)}
如果真实外部条件阻止修复，使用 benchforge_scene_blocker 保存具体证据与缺失条件，然后向用户说明。可执行的下一步必须现在执行，不能只列计划就结束。`;
};

/** Durable host-owned observations. Only the service decides task outcomes. */
/** 创建由宿主持有的持久化采集观察器，协调重载恢复、暂停、去重投递和任务归属。 */
export function createCaptureContinuation({table, artifacts, read, resolveAgent, createMessage, repairImages,
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
  function exhaustedBudget(record, context) {
    if (!record || record.task_seq !== context.taskSeq) return;
    const tasks = record.observation?.tasks || [];
    if (record.observation?.state === 'SUCCEEDED' || tasks.some(task =>
      ['capture_transport','capture_execution','capture_service_contract'].includes(task.failure?.stage))) return;
    const submission = context.captures.find(capture => capture.run_id === record.run_id);
    return exhausted(record) || context.budgets.find(({budget,seq}) =>
      seq > (submission?.seq || 0) && budgetMatches(budget, record.run_id, tasks))?.budget;
  }
  function currentBudget(agent) {
    const context = history(agent.session);
    const latest = context.captures.at(-1);
    if (!latest || context.completed || context.interrupted || waitingForCapture(agent)) return;
    const record = table.get(keyOf(agent.id, latest.run_id));
    return exhaustedBudget(record || {run_id:latest.run_id,task_seq:latest.task_seq}, context);
  }
  /** 只有本轮生产提交已由活跃观察器接管，才能在等待采集时正常收尾。 */
  function waitingForCapture(agent) {
    const context = history(agent.session);
    const latest = context.captures.at(-1);
    if (!latest || context.completed || context.interrupted) return false;
    const key = keyOf(agent.id, latest.run_id);
    const record = table.get(key);
    return record?.state === 'watching' && record.task_seq === context.taskSeq &&
      active.get(key)?.generation === record.generation;
  }
  /** 优先使用已持有的 Agent，否则通过原生控制器恢复记录所有者。 */
  async function ownerOf(record) {
    return agents.get(record.owner) || await resolveAgent(record.owner);
  }
  /** 区分审核服务短暂超时与调用方明确取消，前者不得结束原生对话轮次。 */
  async function currentReview(agent,signal) {
    try {
      return await readReview(agent.session.header.cwd,agent.id,now(),signal);
    } catch (error) {
      signal.throwIfAborted();
      if (error.name==='TimeoutError' || error.code==='BENCHFORGE_REVIEW_STORAGE_UNAVAILABLE') return reviewUnavailable;
      throw error;
    }
  }
  /** 后台持有原作业；审核暂不可读时退避，读取确切状态后再投递。 */
  async function allowed(agent,signal=lifetime) {
    for (let backoff=1000;agent && !history(agent.session).interrupted;backoff=Math.min(30000,backoff*2)) {
      const review=await currentReview(agent,signal);
      signal.throwIfAborted();
      if (review!==reviewUnavailable) return !history(agent.session).interrupted&&!suspendedReview(review);
      await delay(backoff,signal);
    }
    return false;
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
    const budget = exhaustedBudget(record, context);
    // The synchronous rejection already told the agent about this scene.
    if (budget && !exhausted(record)) return save({...record,state:'settled'});
    const key = noticeKey(record,context);
    if (!noticeDelivered(agent,key)) {
      let images = [];
      if (repairImages && record.observation?.tasks.some(task => task.failure?.scene_review?.passed === false)) {
        try {
          images = await repairImages(record.observation.tasks,agent,signal);
        } catch (error) {
          signal.throwIfAborted();
          onError(error);
        }
        // 读图期间会话可能已停止、换任务或由另一观察器投递。
        const after = history(agent.session);
        const stored = table.get(keyOf(record.owner,record.run_id));
        if (after.interrupted || after.completed || after.taskSeq > (record.task_seq || 0) ||
          stored?.generation !== record.generation || ['blocked','paused','settled'].includes(stored?.state) ||
          noticeDelivered(agent,key) || !await allowed(agent,signal)) return stored;
        if (!exhausted(record) && exhaustedBudget(record, history(agent.session)))
          return save({...record,state:'settled'});
      }
      const message = createMessage({source:{kind:'benchforge/capture-watch',noticeKey:key,form:'notice',summary:`采集状态：${outcome(record)}`,
        capture:{run_id:record.run_id,state:outcome(record),failed:captureFailure(record.observation),...(record.error && {error:String(record.error)}),workflow:workflowOf(record),...(budget && {capture_budget:budget})}},
        content:[{type:'text',text:text(record)},...images]});
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
  async function register(agent, submission, {resume = false, observation, observedAfter} = {}) {
    if (closed) throw Error('Capture tracking is unloading; the submitted task is retained by the service');
    const run = captureRunId(submission.run_id), owner = agent.id;
    if (!/^[A-Za-z0-9_-]+$/.test(owner) || !/^sf_[A-Za-z0-9_-]+$/.test(run)) throw Error('Capture tracking requires a native session and run identity');
    agents.set(owner,agent);
    const key = keyOf(owner,run), previous = registration.get(key) || Promise.resolve();
    const operation = previous.catch(()=>{}).then(/** 在同一会话运行队列内建立或升级续接记录，再启动对应代际的宿主观察器。 */ async()=>{
      let record = table.get(key);
      const taskSeq = submission.task_seq ?? history(agent.session).taskSeq;
      const submittedRetry = submission.retry && submission.call_id && record?.generation !== submission.call_id;
      // A retry from outside this host can leave a delivered record behind.
      // Only a service read begun after settlement may reopen that record.
      const observedRetry = resume && pendingRunObservation(observation) &&
        ['terminal','delivered'].includes(record?.state) && record.task_seq === taskSeq &&
        (record.finished_at ?? record.submitted_at) < observedAfter;
      if (!record || submittedRetry || observedRetry) {
        const generation = submittedRetry ? submission.call_id :
          observedRetry ? record.generation + ':resume:' + observedAfter : submission.call_id || run;
        record = await save({owner,run_id:run,generation,task_seq:taskSeq,state:'watching',
          submitted_at:submission.submitted_at || now(),...(observation ? {observation} : {})}, {replace:!!record});
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
  async function wait(agent, run, observation, {observedAfter = now()} = {}) {
    if (observation?.run_id !== captureRunId(run)) throw new Error('Invalid background wait identity');
    if (!pendingRunObservation(observation)) return observation;
    const registered = await register(agent,{run_id:observation.run_id},{resume:true,observation,observedAfter});
    return {...observation,...registered,watch:{...registered.watch,accepted:registered.watch.state === 'watching'}};
  }

  // Only host-owned waits with a live observer for the current task can park a turn.
  function hasPendingWait(agent) {
    const context = history(agent.session);
    if (closed || context.interrupted || context.completed) return false;
    return context.waits.some(run=>{
      const key = keyOf(agent.id,run), row = table.get(key);
      return row?.state === 'watching' && row.task_seq === context.taskSeq &&
        active.get(key)?.generation === row.generation;
    });
  }
  /** 从最新原生采集事件协调续接记录，只恢复仍属于当前任务的运行。 */
  async function reconcile(agent, {recover = false,signal:turnSignal} = {}) {
    const signal=turnSignal?AbortSignal.any([lifetime,turnSignal]):lifetime;
    signal.throwIfAborted();
    const context = history(agent.session);
    if (context.interrupted || context.completed || recover && !context.production) return;
    const latest = context.captures.at(-1);
    if (!latest) return;
    if (waitingForCapture(agent) || currentBudget(agent)) return;
    // Upgrade old production sessions only at their latest capture. Never replay
    // historical failed attempts or acquire an unrelated ordinary conversation.
    const review = await currentReview(agent,signal);
    signal.throwIfAborted();
    if (review===reviewUnavailable) return;
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
    if (context.interrupted || context.completed || !context.activeTurn || hasPendingWait(agent)) return;
    const latest = context.captures.at(-1);
    if (!latest || latest.turn !== context.turn && !context.production) return;
    if (waitingForCapture(agent) || currentBudget(agent)) return;
    const review = await currentReview(agent,signal);
    signal.throwIfAborted();
    if (review===reviewUnavailable) return;
    const updated=history(agent.session);
    if(updated.interrupted||updated.completed||updated.taskSeq!==context.taskSeq)return;
    if (suspendedReview(review)||review?.state==='waiting_user') return;
    await register(agent,latest);
    const record = table.get(keyOf(agent.id,latest.run_id));
    if (['watching','paused','blocked','cancelled'].includes(record.state) || exhaustedBudget(record, updated)) return;
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
      const previous = records(agent.id).filter(row=>row.run_id!==record.run_id &&
        row.scene_id===record.scene_id && row.task_seq===record.task_seq && row.submitted_at<record.submitted_at)
        .sort((a,b)=>b.submitted_at-a.submitted_at)[0];
      if (!record.scene_id || !previous || !repeatedRepairFailure(previous.observation,record.observation))
        throw Error('Two consecutive captures of this scene must document the same unresolved failure or retain the same repair baseline');
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
  return {register,wait,hasPendingWait,reconcile,stopping,block,restore,resume,dispose,settled,records,waitingForCapture,currentBudget};
}
