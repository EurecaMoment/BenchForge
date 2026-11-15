import {stageName, stageSummary} from './stages.mjs';

/** Conversation policy; all facts come from the projection and domain receipts. */
export function createProductionLifecycle({projection,table,agents,readReview,createMessage,restoreAgent=async()=>undefined,now=Date.now,
  schedule=(fn,delay)=>setTimeout(fn,delay),unschedule=clearTimeout,onError=()=>{}}) {
  const timers=new Map(), queues=new Map(), reminders=new Map();
  let disposed=false;
  const serialized=(id,operation)=>{
    const previous=queues.get(id)||Promise.resolve();
    const next=previous.catch(()=>{}).then(operation);
    queues.set(id,next);
    return next.finally(()=>{if(queues.get(id)===next)queues.delete(id);});
  };
  const viewOf=agent=>projection.stateOf(agent.session).view;
  const notice=(kind,key,text)=>createMessage({source:{kind,noticeKey:key,form:'notice',summary:text},content:[{type:'text',text}]});
  function pendingNotice(agent,key) {
    const pending=[...(agent.inbox?.nextTurn||[]),...(agent.inbox?.nextStep||[])];
    return pending.some(message=>message.source?.noticeKey===key);
  }
  function arm(id,record) {
    if(timers.has(id))unschedule(timers.get(id));
    timers.delete(id);
    if(disposed||!record.review||record.suspended) return;
    const timer=schedule(()=>{timers.delete(id);return refresh(id).catch(onError);},Math.max(0,record.review.deadline*1000-now()));
    timer?.unref?.();timers.set(id,timer);
  }
  async function observe(agent) {
    if(disposed)return;
    const view=viewOf(agent);
    if(!view.active || agent.session.header?.origin==='subagent')return;
    await serialized(agent.id,async()=>{
      const prior=table.get(agent.id);
      const key=view.review ? `${agent.id}:${view.review.review_id}` : '';
      const record={sessionId:agent.id,workspace:view.workspace,plan:view.plan,run:view.run,
        revision:view.revision,review:view.review,key,suspended:false};
      if(['paused','needs_input','needs_evidence','needs_plan_revision','failed','completed'].includes(view.state)) record.suspended=true;
      if(prior?.key===key&&prior.suspended&&view.state==='interrupted')record.suspended=true;
      const stable=value=>JSON.stringify(value && {...value,revision:0});
      if(stable(prior)===stable(record))return;
      await table.put(agent.id,record);
      arm(agent.id,record);
    });
  }
  async function refresh(id) {
    return serialized(id,async()=>{
      if(disposed)return false;
      const record=table.get(id);
      if(!record?.review||record.suspended)return false;
      const review=await readReview(record.workspace,id,record.review.review_id,now());
      if(['paused','needs_input','superseded'].includes(review.state)) {
        await table.put(id,{...record,suspended:true});return false;
      }
      if(review.state==='waiting_user') {arm(id,{...record,review:{...record.review,deadline:review.deadline}});return false;}
      const agent=agents.get(id)||await restoreAgent(id);
      if(!agent||disposed)return false;
      const state=projection.stateOf(agent.session),view=state.view;
      if(view.state==='interrupted'&&review.state!=='continued') {
        // Unload is recoverable; explicit user cancellation must remain stopped.
        if(state.interruption!=='disposed') {
          await table.put(id,{...record,suspended:true});return false;
        }
      }
      if(!['elapsed','continued'].includes(review.state))return false;
      if(pendingNotice(agent,record.key))return true;
      if(state.notices.includes(record.key))return agent.status==='running';
      const message=notice('benchforge/production-resume',record.key,
        `生产阶段通知（系统状态）：${review.stage} ${stageName(review.stage)} 的用户窗口已结束。读取 benchforge_stage_review 的当前状态，处理已有用户指令，然后使用 benchforge_pipeline_run 继续同一个任务。阶段含义：${stageSummary()}，S3 包含标注。workspace=${record.workspace}；plan=${record.plan}；run=${record.run || '尚未创建'}。不要重建已完成阶段。先向用户简短说明当前阶段和下一步。`);
      if(agent.status==='running')agent.steer(message);else agent.followup(message);
      // A queued notice may be lost before its first step is committed. The inbox
      // and standard session events, not a prior enqueue, establish delivery.
      return true;
    });
  }
  async function preStep({agent,signal},next) {
    const decision=await next();
    if(decision.kind!=='enter'||signal?.aborted)return decision;
    signal?.throwIfAborted();
    const view=viewOf(agent);
    await observe(agent);
    if(view.active&&view.workflowTurn===view.turn&&agent.status==='running') {
      const previous=reminders.get(agent.id);
      const silent=now()-view.lastTextAt>=60000;
      const stageChanged=view.review&&previous?.review!==view.review.review_id;
      const failed=view.error&&previous?.error!==view.error;
      if((silent||stageChanged||failed)&&(!previous||now()-previous.at>=60000)) {
        reminders.set(agent.id,{...previous,at:now(),review:view.review?.review_id,error:view.error});
        // A review receipt in the projection can predate the user's Continue.
        // Read the domain before describing a wait; the UI is not its authority.
        const review=view.review ? await readReview(view.workspace,agent.id,view.review.review_id,now()) : null;
        const pending=review&&['waiting_user','paused','needs_input'].includes(review.state);
        const reviewNotice=review ? `展示真实阶段产物：${review.artifact || view.review.artifact}；`+
          (pending ? '审核仍未放行，处理当前用户指令或等待原窗口，不重新计时。' :
            review.state==='superseded' ? '已有更新的阶段回执，请读取当前状态。' :
              '该审核已放行，继续执行并报告真实执行错误；不要再次等待或因执行修补重开 S1。') : '';
        const message=notice('benchforge/production-progress',`${agent.id}:${view.revision}`,
          `进度沟通提醒（系统状态）：请在继续工具调用前，用用户的语言简短说明已完成的工作、当前阶段、实际阻塞和下一步。最近记录阶段=${stageName(view.stage)||'计划'}，状态=${view.state}，本任务工具调用=${view.tools}，shell=${view.shells}。S2 是场景选取或新建，S3 是采集（包含标注）。${view.error?'最近调用失败，请说明实际原因并修正入口。':''}${reviewNotice}不要展示隐藏推理，不要把调用次数或文件数量当成完成比例。`);
        return {...decision,messages:[...decision.messages,message]};
      }
    }
    return decision;
  }
  async function stopping({agent,turn,signal}) {
    signal?.throwIfAborted();
    const view=viewOf(agent);
    if(!view.active||view.workflowTurn!==view.turn)return;
    await observe(agent);
    const key=`${agent.id}:${turn}:${view.state}:${view.review?.review_id||0}`;
    const last=reminders.get(agent.id);
    if(last?.stop===key)return;
    if(view.lastTextSeq<0 || now()-view.lastTextAt>60000 || view.state==='completed'&&!view.delivered) {
      reminders.set(agent.id,{...last,stop:key});
      agent.steer(notice('benchforge/production-delivery',key,
        `本轮收尾检查：当前生产状态=${view.state}。请给出面向用户的结果说明和真实产物链接；已完成交付使用 present。未完成时说明具体缺项和下一步，不能宣告完成。等待用户窗口时可以结束当前轮，系统会在窗口结束后通知继续。`));
    }
  }
  async function resume(agent) {
    const record=table.get(agent.id);
    // agent/created is composition time. Queue after creation, never drive it here.
    if(record?.review&&!record.suspended)arm(agent.id,record);
  }
  function start() {
    for(const [id,record] of table.entries())arm(id,record);
  }
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
    async dispose(){disposed=true;for(const timer of timers.values())unschedule(timer);timers.clear();await Promise.allSettled([...queues.values()]);},
  };
}
