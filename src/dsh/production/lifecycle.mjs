import {stageName, stageSummary} from './stages.mjs';
import {waitWithSignal} from '../runtime/cancellation.mjs';
import {ProductionIncompleteError} from './errors.mjs';
import {reviewSnapshot, reviewNotice, reviewChanges} from './review_snapshot.mjs';

/** Conversation policy; all facts come from the projection and domain receipts. */
/** 创建生产任务生命周期控制器，按会话协调复核、停止恢复、通知去重和宿主卸载。 */
export function createProductionLifecycle({projection,table,agents,readReview,advanceReview=readReview,createMessage,restoreAgent=async()=>undefined,reconcileCaptures=async()=>{},now=Date.now,
  waitingForCapture=()=>false,schedule=(fn,delay)=>setTimeout(fn,delay),unschedule=clearTimeout,onError=()=>{},maxStopRecoveries=2}) {
  const timers=new Map(), queues=new Map(), reminders=new Map(), checks=new Map();
  const lifetime=new AbortController();
  let disposed=false;
  /** 将同一会话的状态变更串行排队；等待与执行均响应取消，完成后删除队列占用。 */
  const serialized=(id,operation,signal=lifetime.signal)=>{
    const previous=queues.get(id)||Promise.resolve();
    const next=waitWithSignal(previous,signal).catch(_priorFailure=>{signal?.throwIfAborted();}).then(()=>{signal?.throwIfAborted();return operation();});
    queues.set(id,next);
    const settled=next.finally(()=>{if(queues.get(id)===next)queues.delete(id);});
    return waitWithSignal(settled,signal);
  };
  /** 取得会话级检查信号，并与整个生命周期的取消信号合并。 */
  function checkSignal(id) {
    if(!checks.has(id))checks.set(id,{controller:new AbortController()});
    return AbortSignal.any([lifetime.signal,checks.get(id).controller.signal]);
  }
  /** 将当前轮次的取消信号绑定到后台检查，避免停止后的旧任务继续投递消息。 */
  function bindTurn(agent,signal) {
    if(!signal || checks.get(agent.id)?.signal===signal)return;
    const prior=checks.get(agent.id);
    prior?.detach?.();
    const controller=prior&&!prior.controller.signal.aborted?prior.controller:new AbortController();
    /** 停止该会话的检查和定时器，并持久化暂停状态供显式恢复使用。 */
    const abort=()=>{
      controller.abort(signal.reason);
      if(timers.has(agent.id))unschedule(timers.get(agent.id));
      timers.delete(agent.id);
      if(signal.reason?.kind!=='disposed'&&!disposed)void serialized(agent.id,async()=>{
        const record=table.get(agent.id);
        if(record)await table.put(agent.id,{...record,suspended:true});
      }).catch(error=>{if(!disposed)onError(error);});
    };
    signal.addEventListener('abort',abort,{once:true});
    checks.set(agent.id,{controller,signal,/** 解除旧轮次的取消监听，避免重复处理后续停止事件。 */ detach:()=>signal.removeEventListener('abort',abort)});
    if(signal.aborted)abort();
  }
  /** 读取该 Agent 会话当前的生产投影视图。 */
  const viewOf=agent=>projection.stateOf(agent.session).view;
  /** 构造带稳定通知键和业务事实的原生提示消息。 */
  const notice=(kind,key,text,facts={})=>createMessage({source:{kind,noticeKey:key,form:'notice',summary:text,...facts},content:[{type:'text',text}]});
  /** 检查下一轮或下一步收件箱是否已包含同一通知，避免重复投递。 */
  function pendingNotice(agent,key) {
    const pending=[...(agent.inbox?.nextTurn||[]),...(agent.inbox?.nextStep||[])];
    return pending.some(message=>message.source?.noticeKey===key);
  }
  /** 按复核截止时间安排后台刷新；暂停、取消或卸载后不再设定定时器。 */
  function arm(id,record,minDelay=0) {
    if(timers.has(id))unschedule(timers.get(id));
    timers.delete(id);
    if(disposed||checks.get(id)?.controller.signal.aborted||!record.review||record.suspended) return;
    const timer=schedule(()=>{
      timers.delete(id);
      return refresh(id).catch(error=>{if(!disposed&&!checks.get(id)?.controller.signal.aborted){onError(error);arm(id,table.get(id)||record,5000);}});
    },Math.max(minDelay,record.review.deadline*1000-now()));
    timer?.unref?.();timers.set(id,timer);
  }
  /** 在同会话队列内读取当前复核并更新持久化状态，只处理活动的父生产会话。 */
  async function observe(agent,signal=lifetime.signal) {
    if(disposed)return;
    signal=AbortSignal.any([lifetime.signal,signal]);
    const view=viewOf(agent);
    if(!view.active || agent.session.header?.origin==='subagent')return;
    return serialized(agent.id,async()=>{
      const prior=table.get(agent.id);
      let current=null;
      if(view.review) {
        try {
          current=await waitWithSignal(readReview(view.workspace,agent.id,view.review.review_id,now(),signal),signal);
        } catch(error) {
          signal.throwIfAborted();
          if(error.code!=='BENCHFORGE_REVIEW_STORAGE_UNAVAILABLE' && error.name!=='TimeoutError')throw error;
          // 观察失败不覆盖审核记录，工具准入仍独立核对后端。
          return {reviewUnavailable:true};
        }
      }
      signal?.throwIfAborted();
      const review=reviewSnapshot(current,view.review) || view.review;
      const key=review ? `${agent.id}:${review.review_id}` : '';
      const record={sessionId:agent.id,workspace:view.workspace,plan:view.plan,run:view.run,
        revision:view.revision,review,key,suspended:false};
      // Tool-result projections can predate a backend round or user control.
      // Never replace the authoritative deadline with that older snapshot.
      if(['paused','needs_input','check_failed','superseded'].includes(current?.state) ||
        ['needs_evidence','needs_plan_revision','failed','completed'].includes(view.state)) record.suspended=true;
      if(prior?.key===key&&prior.suspended&&view.state==='interrupted')record.suspended=true;
      /** 忽略投影修订号来比较复核内容是否真正改变。 */
      const stable=value=>JSON.stringify(value && {...value,revision:0});
      if(stable(prior)===stable(record))return {...record,reviewReceipt:current};
      await table.put(agent.id,record);
      signal?.throwIfAborted();
      arm(agent.id,record);
      return {...record,reviewReceipt:current};
    },signal);
  }
  /** 推进已登记复核并处理释放、暂停及输入要求，再按新状态决定是否通知或继续检查。 */
  async function refresh(id) {
    const signal=checkSignal(id);
    return serialized(id,async()=>{
      if(disposed)return false;
      const record=table.get(id);
      if(!record?.review||record.suspended)return false;
      const review=await waitWithSignal(advanceReview(record.workspace,id,record.review.review_id,now(),signal),signal);
      signal.throwIfAborted();
      const updated={...record,review:reviewSnapshot(review,record.review) || record.review};
      if(['paused','needs_input','superseded'].includes(review.state)) {
        await table.put(id,{...updated,suspended:true});return false;
      }
      if(review.state==='waiting_user') {
        await table.put(id,updated);
        arm(id,updated,1000);return false;
      }
      const agent=agents.get(id)||await restoreAgent(id);
      signal.throwIfAborted();
      if(!agent||disposed)return false;
      const state=projection.stateOf(agent.session),view=state.view;
      if(view.state==='interrupted') {
        // Unload is recoverable; explicit user cancellation must remain stopped.
        if(state.interruption!=='disposed') {
          await table.put(id,{...record,suspended:true});return false;
        }
      }
      if(review.state==='check_failed') {
        await table.put(id,{...updated,suspended:true});
        const key=`${record.key}:check-failed:${review.last_check?.checked_at||0}`;
        if(!pendingNotice(agent,key)&&!state.notices.includes(key)) {
          const message=notice('benchforge/production-check-failed',key,
            `阶段自检未通过，自动继续已停止：${review.check_failure?.error||'请读取 benchforge_stage_review 当前检查结果'}。保留当前审核和已完成等待，修复产物后调用 benchforge_stage_review(action=retry)。先向用户说明实际缺项，不能把等待结束当成检查通过。`,reviewNotice(review,view.review));
          if(agent.status==='running')agent.steer(message);else agent.followup(message);
        }
        return false;
      }
      if(!['elapsed','continued'].includes(review.state))return false;
      await table.put(id,updated);
      if(['needs_evidence','needs_plan_revision','failed','completed'].includes(view.state))return false;
      await reconcileCaptures(agent,signal);
      signal.throwIfAborted();
      const recovered=state.notices.includes(record.key)&&view.state==='interrupted'&&state.interruption==='disposed';
      const key=recovered ? `${record.key}:disposal:${state.interruptionSeq}` : record.key;
      if(pendingNotice(agent,key))return true;
      if(state.notices.includes(key))return agent.status==='running';
      const message=notice('benchforge/production-resume',key,
        `生产阶段通知（系统状态）：${review.stage} ${stageName(review.stage)} 的审核已放行。${review.state==='elapsed'?'三轮自检和等待已完成。':'用户已要求继续，产物检查已通过。'}读取 benchforge_stage_review 的当前状态，处理已有用户指令，然后使用 benchforge_pipeline_run 继续同一个任务。阶段含义：${stageSummary()}，S3 包含标注。workspace=${record.workspace}；plan=${record.plan}；run=${record.run || '尚未创建'}。不要重建已完成阶段。先向用户简短说明当前阶段和下一步。`,reviewNotice(review,view.review));
      if(agent.status==='running')agent.steer(message);else agent.followup(message);
      // A queued notice may be lost before its first step is committed. The inbox
      // and standard session events, not a prior enqueue, establish delivery.
      return true;
    },signal);
  }
  /** 在 Agent 准入下一步时协调生产状态，并根据静默时长和阶段变化补充必要提醒。 */
  async function preStep({agent,signal},next) {
    bindTurn(agent,signal);
    const decision=await next();
    signal?.throwIfAborted();
    if(decision.kind!=='enter')return decision;
    const view=viewOf(agent);
    const observed=await observe(agent,signal);
    if(observed?.reviewUnavailable)return decision;
    if(view.active&&view.workflowTurn===view.turn&&agent.status==='running') {
      const previous=reminders.get(agent.id);
      const silent=now()-view.lastTextAt>=60000;
      const stageChanged=view.review&&previous?.review!==view.review.review_id;
      const failed=view.error&&previous?.error!==view.error;
      const facts=reviewNotice(observed?.review,view.review);
      const reviewChanged=facts.review && JSON.stringify(facts.review)!==JSON.stringify(view.review);
      if(reviewChanged || (silent||stageChanged||failed)&&(!previous||now()-previous.at>=60000)) {
        reminders.set(agent.id,{...previous,at:now(),review:view.review?.review_id,error:view.error});
        // A review receipt in the projection can predate the user's Continue.
        // Read the domain before describing a wait; the UI is not its authority.
        const review=observed?.reviewReceipt || observed?.review;
        signal?.throwIfAborted();
        const pending=review&&['waiting_user','paused','needs_input','check_failed'].includes(review.state);
        const reviewNotice=review ? `展示真实阶段产物：${review.artifact || view.review.artifact}；`+
          (pending ? (review.state==='check_failed' ? '真实产物自检失败，修复后重试，不能靠超时放行。' : `审核仍未放行，已完成等待 ${review.waits_completed||0}/3；处理当前用户指令或等待本轮，不重新计时。`) :
            review.state==='superseded' ? '已有更新的阶段回执，请读取当前状态。' :
              '该审核已放行，继续执行并报告真实执行错误；不要再次等待或因执行修补重开 S1。') : '';
        const message=notice('benchforge/production-progress',`${agent.id}:${view.revision}`,
          `进度沟通提醒（系统状态）：请在继续工具调用前，用用户的语言简短说明已完成的工作、当前阶段、实际阻塞和下一步。最近记录阶段=${stageName(view.stage)||'计划'}，状态=${reviewChanges(view,facts).state || view.state}，本任务工具调用=${view.tools}，shell=${view.shells}。S2 是场景选取或新建，S3 是采集（包含标注）。${view.error?'最近调用失败，请说明实际原因并修正入口。':''}${reviewNotice}不要展示隐藏推理，不要把调用次数或文件数量当成完成比例。`,facts);
        return {...decision,messages:[...decision.messages,message]};
      }
    }
    return decision;
  }
  /** 结束轮次前核对实际生产与复核状态，对可恢复的未完成工作安排有界续接。 */
  async function stopping({agent,turn,signal}) {
    signal?.throwIfAborted();
    const state=projection.stateOf(agent.session);
    let view=state.view;
    if(!view.active||view.workflowTurn!==view.turn)return;
    const observed=await observe(agent,signal);
    signal?.throwIfAborted();
    if(observed?.reviewUnavailable) {
      const key=`${agent.id}:${turn}:review-unavailable`;
      const last=reminders.get(agent.id);
      if(last?.unavailable!==key && !state.notices.includes(key)) {
        reminders.set(agent.id,{...last,unavailable:key});
        agent.steer(notice('benchforge/production-continue',key,'阶段审核服务暂时无法读取。报告已保存的进展和阻塞；恢复后读取原审核，再继续依赖该审核的生产。'));
      }
      return;
    }
    const review=observed?.reviewReceipt;
    view={...view,...reviewChanges(view,reviewNotice(review,view.review))};
    const waiting=review&&['waiting_user','paused','needs_input','check_failed'].includes(review.state);
    const blocked=['needs_input','needs_evidence','needs_plan_revision','blocked','failed','paused'].includes(view.state);
    const delivered=view.state==='completed'&&view.delivered;
    const visible=state.lastResponse?.visible ?? view.lastTextSeq>=0;
    if((waiting||blocked||delivered)&&visible)return;
    if(visible && view.state==='waiting_capture' && waitingForCapture(agent))return;
    const key=`${agent.id}:${turn}:${view.state}:${view.plan}:${view.run}:${view.completed}:${review?.review_id||0}:${review?.state||''}`;
    const last=reminders.get(agent.id);
    if(last?.stop===key&&last.response===state.lastResponse?.seq)return;
    const attempts=Math.max(state.notices.filter(value=>value.startsWith(key+':recovery:')).length,last?.stop===key?last.attempts||0:0);
    if(attempts>=maxStopRecoveries)throw new ProductionIncompleteError();
    reminders.set(agent.id,{...last,stop:key,response:state.lastResponse?.seq,attempts:attempts+1});
    const action=!view.plan?'计划尚未保存。使用已读合同保存计划草稿并调用 benchforge_pipeline_plan；不要反复遍历源码。':
      waiting?'当前阶段等待关口仍有效，不得提前执行依赖阶段。':blocked?'如实说明当前阻塞、已保存产物和所需输入。':
        view.state==='completed'?'已完成交付使用 present，并向用户给出真实产物链接。':'沿已保存计划继续执行下一项未完成工作；不要重做已完成阶段。';
    agent.steer(notice('benchforge/production-continue',`${key}:recovery:${attempts+1}`,
      `本轮收尾检查：当前生产状态=${view.state}，尚不能据此宣告任务完成。${action}先简短说明当前进展；不能用空回复结束。若存在无法自行修复的阻塞，使用已有任务或阶段工具记录真实阻塞并解释，不能伪造验收。`,reviewNotice(review,view.review)));
  }
  /** 在 Agent 恢复后重新安排未暂停复核的检查，不在会话组合阶段直接驱动工作。 */
  async function resume(agent) {
    const record=table.get(agent.id);
    // agent/created is composition time. Queue after creation, never drive it here.
    if(record?.review&&!record.suspended)arm(agent.id,record);
  }
  /** 宿主启动后立即协调持久化的复核窗口，使过期记录获得一次及时检查。 */
  function start() {
    // Reconcile persisted/legacy windows immediately after host startup.
    for(const [id,record] of table.entries())arm(id,{...record,
      review:record.review && {...record.review,deadline:Math.min(record.review.deadline,now()/1000)}});
  }
  /** 显式解除会话复核的暂停状态，并立即刷新对应后台状态。 */
  async function changed(id) {
    const ready=await serialized(id,async()=>{
      const record=table.get(id);
      if(!record?.review)return false;
      await table.put(id,{...record,suspended:false});
      return true;
    });
    return ready ? refresh(id) : false;
  }
  return {observe,preStep,stopping,resume,start,refresh:changed,
    /** 取消全部生命周期任务、移除监听和定时器，并等待已排队操作结束。 */
    async dispose(){disposed=true;lifetime.abort(new DOMException('Production lifecycle unloaded','AbortError'));
      for(const check of checks.values()){check.detach?.();check.controller.abort(lifetime.signal.reason);}checks.clear();
      for(const timer of timers.values())unschedule(timer);timers.clear();await Promise.allSettled([...queues.values()]);},
  };
}
