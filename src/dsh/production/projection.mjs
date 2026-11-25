import {z} from 'zod';
import {canonicalToolName} from '../tools/invocation/identifiers.mjs';
import {backgroundWaitRun, trackedSubmission} from '../tools/scene/capture/capture_watch.mjs';
import {terminalCapture} from '../tools/scene/capture/continuation_state.mjs';
import {isProductionRequest} from './request.mjs';
import {ReviewSnapshot, currentReviewSnapshot, reviewChanges, reviewSnapshot} from './review_snapshot.mjs';
import {budgetMatches, budgetRejection, captureProducers} from '../tools/scene/capture/budget.mjs';
import {STAGES} from './stages.mjs';

export const PRODUCTION_PROJECTION = 'benchforgeProduction';
export const ProductionView = z.object({
  revision:z.number(), active:z.boolean(), running:z.boolean(), turn:z.number(), workflowTurn:z.number(),
  startedAt:z.number(), lastTextAt:z.number(), lastTextSeq:z.number(), lastEventAt:z.number(),
  lastTool:z.string(), tools:z.number(), shells:z.number(), models:z.array(z.string()),
  workspace:z.string(), plan:z.string(), run:z.string(), state:z.string(), stage:z.string(),
  error:z.string(), review:ReviewSnapshot.nullable(), completed:z.number(), total:z.number(), delivered:z.boolean(),
  pendingCaptures:z.array(z.object({run:z.string(),name:z.string()})), stopReason:z.string(),
  capture_budget:z.record(z.string(),z.unknown()).nullable().optional(),
});
const State = z.object({view:ProductionView, planReview:ReviewSnapshot.nullable(), errorTool:z.string(), errorCallSeq:z.number(), notices:z.array(z.string()), interruption:z.string(), interruptionSeq:z.number(),
  progressNotice:z.object({fingerprint:z.string(),textSeq:z.number(),at:z.number()}).nullable(),
  captureRun:z.string().optional(),
  lastResponse:z.object({seq:z.number(),visible:z.boolean()}),
  calls:z.record(z.string(), z.object({name:z.string(), workspace:z.string(), plan:z.string(), scene:z.string(), seq:z.number()}))});
/** 提取消息中的可见文本块，合并并去掉两端空白。 */
const visible = message => (message?.content || []).filter(block => block.type === 'text').map(block => block.text).join('\n').trim();
/** 尝试解析事件中的 JSON 文本，解析失败时返回空值供投影忽略。 */
const parse = text => {try {return JSON.parse(text);} catch {return null;}};
const modelTools = new Set(['benchforge_diffusion','benchforge_scene_sam3','benchforge_depth','benchforge_trellis','benchforge_isaac_sim']);
const executionStages = new Set(STAGES.slice(1).map(stage=>stage.id));
/** 计划的全部步骤都已完成；用户重新发起生产后状态会变化，但步数仍能证明上一次运行已结束。 */
const runFinished = view => view.total>0 && view.completed>=view.total;
const workflowReceipts = new Set(['benchforge_pipeline_plan','benchforge_pipeline_run','benchforge_pipeline_status','benchforge_stage_review']);
/** 合并生产视图变化，并保留 S1 复核回执供后续阶段判断同一计划的执行修补。 */
const patch = (state, event, changes) => ({...state,planReview:changes.review?.stage==='S1'?changes.review:state.planReview,
  view:{...state.view, revision:event.seq, lastEventAt:event.time || 0, ...changes}});
/** 仅在服务端确认同一计划复用已放行的 S1 审核时，保留已进入后续阶段的执行状态。 */
function retainsExecution(state,call,receipt,snapshot) {
  const revision=receipt.review_revision;
  return state.view.active && executionStages.has(state.view.stage) && call.name==='benchforge_pipeline_plan' &&
    receipt.production_contract==='benchforge.production-stages/v1' && receipt.state==='planned' &&
    typeof receipt.plan==='string' && Boolean(receipt.plan) && revision?.kind==='execution_repair' &&
    revision.review_reused===true && Boolean(state.view.plan) && revision.base_plan===state.view.plan &&
    snapshot?.stage==='S1' && snapshot.review_id===state.planReview?.review_id && ['elapsed','continued'].includes(snapshot.state);
}

export const productionProjection = {
  key:PRODUCTION_PROJECTION, stateVersion:17, stateSchema:State,
  /** 根据会话头建立空生产状态，初始化工具追踪、通知和阶段进度字段。 */
  init:header => ({calls:{},planReview:null,progressNotice:null,errorTool:'',errorCallSeq:0,notices:[],interruption:'',interruptionSeq:0,lastResponse:{seq:-1,visible:false},view:{revision:0,active:false,running:false,turn:0,workflowTurn:0,startedAt:0,lastTextAt:0,lastTextSeq:-1,
    lastEventAt:0,lastTool:'',tools:0,shells:0,models:[],workspace:header.cwd || '',plan:'',run:'',state:'idle',stage:'',
    error:'',pendingCaptures:[],stopReason:'',review:null,completed:0,total:0,delivered:false}}),
  /** 把原生轮次、工具和通知事件归约为生产状态；批量工具派发沿用标准调用与结果处理。 */
  apply(state,event) {
    if (event.type === 'tool/ptc-dispatch') {
      const data=event.data;
      const called=productionProjection.apply(state,{...event,type:'tool/call',data:{callId:data.subCallId,name:data.name,arguments:JSON.stringify(data.arguments)}});
      return productionProjection.apply(called,{...event,type:'tool/result',data:{meta:data.meta,
        message:{toolCallId:data.subCallId,isError:data.isError,content:data.content}}});
    }
    const {view} = state;
    if(event.type === 'turn/start') return patch({...state,interruption:'',interruptionSeq:0,lastResponse:{seq:-1,visible:false}},event,{running:true,stopReason:'',turn:event.data.turn,startedAt:event.time,lastTextAt:event.time,lastTextSeq:-1,delivered:false});
    if(event.type === 'turn/end') return patch({...state,...(event.data.reason?.kind==='error'?{errorTool:''}:{}),interruption:event.data.reason?.reason?.kind || '',interruptionSeq:event.seq},event,{running:false,
      stopReason:event.data.reason?.kind==='completed'?'':event.data.reason?.reason?.reason || event.data.reason?.reason?.kind || event.data.reason?.kind || '',
      ...(event.data.reason?.kind==='error'?{error:event.data.reason.error?.message || 'Production turn failed'}:{}),
      ...(event.data.reason?.kind === 'aborted' ? {state:'interrupted'} :
        view.active && view.workflowTurn===view.turn && !['completed','waiting_user','paused','needs_input','needs_evidence','needs_plan_revision','blocked','failed'].includes(view.state)
          ? {state:event.data.reason?.kind==='completed' && view.pendingCaptures.length ? 'waiting_capture' : 'incomplete'} : {})});
    if(event.type === 'user/message' && isProductionRequest(event.data))
      return patch({...state,errorTool:''},event,{active:true,workflowTurn:view.turn,state:view.plan?'running':'planning',stage:view.stage||'S1',error:'',capture_budget:null});
    if(event.type === 'user/message' && event.data.source?.kind==='benchforge/production-progress')
      return patch({...state,progressNotice:event.data.source.progress || state.progressNotice},event,reviewChanges(view,event.data.source));
    if(event.type === 'user/message' && ['benchforge/production-resume','benchforge/production-check-failed','benchforge/production-continue'].includes(event.data.source?.kind)) {
      const key=event.data.source.noticeKey;
      return typeof key==='string'&&!state.notices.includes(key)
        ? patch({...state,notices:[...state.notices.slice(-63),key]},event,{workflowTurn:view.turn,...reviewChanges(view,event.data.source)}) : state;
    }
    if(event.type === 'user/message' && event.data.source?.kind === 'benchforge/capture-watch' && view.active) {
      const capture = event.data.source.capture;
      const pendingCaptures=view.pendingCaptures.filter(row=>row.run!==capture?.run_id);
      // 运行完成后的采集不属于该次生产，只更新待返回列表。
      if (view.state==='completed') return patch(state,event,{pendingCaptures});
      const otherScene = state.captureRun && capture?.run_id !== state.captureRun;
      if (capture && ((otherScene && capture.capture_budget) || (view.capture_budget && otherScene)))
        return pendingCaptures.length === view.pendingCaptures.length ? state : patch(state,event,{pendingCaptures});
      if(capture?.state==='SUCCEEDED' && capture.workflow && capture.workflow!=='scene' && !capture.failed)
        return patch(state,event,{pendingCaptures,workflowTurn:view.turn,...(view.state==='waiting_capture'?{state:'running'}:{})});
      // 历史计划曾把展示退回 S1；随后真实采集回执证明已进入场景阶段，重放即可恢复。
      const sceneStage=(!capture?.workflow || capture.workflow==='scene') &&
        view.stage==='S1' && (!view.review || ['elapsed','continued'].includes(view.review.state));
      if(capture) return patch({...state,errorTool:''},event,
        {pendingCaptures,workflowTurn:view.turn,...(sceneStage ? {stage:'S2',review:null} : {}),
          capture_budget:capture.capture_budget || view.capture_budget || null,
          state:capture.capture_budget||capture.blocked?'blocked':capture.failed?'needs_repair':
          capture.state==='SUCCEEDED'?'scene_ready':'capture_error',
        error:capture.capture_budget?'本场景的正式采集预算已用完。':capture.blocked?(event.data.source.summary || '执行受阻，任务未完成。'):
          capture.state==='observer_failed'?(capture.error || '后台跟踪失败，请恢复原作业跟踪。'):capture.failed?(capture.error || '后台作业或场景验收失败。读取实际报告并修复。'):''});
    }
    if(event.type === 'assistant/message') {
      const hasText=Boolean(visible(event.data.message));
      return patch({...state,lastResponse:{seq:event.seq,visible:hasText}},event,hasText?{lastTextAt:event.time,lastTextSeq:event.seq}:{});
    }
    if(event.type === 'deliverables/presented') return patch(state,event,{delivered:true});
    if(event.type === 'tool/call') {
      const {callId} = event.data, name = canonicalToolName(event.data.name), args = parse(event.data.arguments) || {};
      if(!name.startsWith('benchforge_')&&!view.active)return state;
      const next = {...state,calls:{...state.calls,[callId]:{name,workspace:args.workspace || view.workspace,plan:args.plan || '',scene:typeof args.name==='string'?args.name:(typeof args.scene_program_path==='string'?args.scene_program_path.split(/[\\/]/).pop():''),seq:event.seq}}};
      const workflow=['benchforge_pipeline_plan','benchforge_pipeline_run'].includes(name);
      return patch(next,event,{...(workflow?{active:true,workflowTurn:view.turn,...(!view.active?{state:'planning',stage:'S1'}:{})}:{}),
        lastTool:name,tools:view.tools+1,shells:view.shells+(name==='bash'?1:0),
        models:modelTools.has(name)&&!view.models.includes(name)?[...view.models,name]:view.models});
    }
    if(event.type !== 'tool/result' || event.surfaceOp === 'replace') return state;
    const message = event.data.message, callId = message?.source?.callId || message?.toolCallId;
    const recorded = state.calls[callId];
    const resolved = event.data.meta?.benchforgeInvocation?.resolved;
    const call = recorded && {...recorded, name: resolved?.name || recorded.name,
      workspace: resolved?.arguments?.workspace || recorded.workspace};
    if(!call) return state;
    const calls = {...state.calls}; delete calls[callId];
    const next = {...state,calls};
    if(!call.name.startsWith('benchforge_')) return next;
    // 大回执的可见文本会被宿主截断，流程工具另在 meta 中附带精简回执。
    const text = visible(message);
    const receipt = parse(text) ?? (workflowReceipts.has(call.name) ? event.data.meta?.benchforgeReceipt : null) ?? null;
    const budget = captureProducers.has(call.name) && budgetRejection(receipt || text);
    if (budget && view.active && ['S1','S2'].includes(view.stage)) {
      if (!state.captureRun || budgetMatches(budget, state.captureRun))
        return patch({...next,errorTool:call.name,errorCallSeq:call.seq},event,{workflowTurn:view.turn,
          stage:'S2',state:'blocked',capture_budget:budget,error:receipt?.detail || '本场景的正式采集预算已用完。'});
      return next;
    }
    if(message?.isError || event.data.error) return patch({...next,errorTool:call.name,errorCallSeq:call.seq},event,{error:text.slice(0,700)});
    if(!receipt || typeof receipt !== 'object') return next;
    // 计划预检只描述准备稿，不能覆盖正在执行的阶段、运行和审核。
    if(call.name==='benchforge_pipeline_plan' && receipt.state!=='planned') {
      if(receipt.state==='needs_execution_repair') return patch(next,event,{
        state:'needs_repair',stage:receipt.stage || view.stage,plan:receipt.plan || view.plan,
        run:receipt.run || view.run,error:receipt.error || '',workflowTurn:view.turn,
        completed:receipt.completed ?? view.completed,total:receipt.total_steps ?? view.total,
        review:view.review?.stage===receipt.stage ? view.review : null,
      });
      if(executionStages.has(view.stage)) return patch(next,event,
        receipt.issues?.length ? {error:receipt.issues.join('\n').slice(0,700)} : {});
    }
    const activated = receipt.production_contract === 'benchforge.production-stages/v1';
    const review = receipt.stage_review || (call.name==='benchforge_stage_review'?receipt:null);
    const snapshot=reviewSnapshot(review,view.review);
    // 后续阶段可能已清空当前复核；仍用保留的 S1 版本拒绝过期计划回执。
    if(snapshot?.stage==='S1' && state.planReview && !currentReviewSnapshot(state.planReview,snapshot,{allowNewReview:true})) return next;
    // 后续阶段读取已放行的 S1 历史回执属于查询，不能令进度退回计划阶段。
    if(call.name==='benchforge_stage_review' && executionStages.has(view.stage) &&
        snapshot?.stage==='S1' && snapshot.review_id===state.planReview?.review_id &&
        ['elapsed','continued'].includes(snapshot.state)) return {...next,planReview:snapshot};
    // 运行完成后读取已放行的审核属于查询，不能让已完成的生产退回执行中。
    if(call.name==='benchforge_stage_review' && view.state==='completed' &&
        snapshot && ['elapsed','continued'].includes(snapshot.state)) return next;
    const retained=retainsExecution(state,call,receipt,snapshot);
    if(call.name==='benchforge_pipeline_plan' && receipt.state==='planned' &&
        executionStages.has(view.stage) && receipt.production_contract==='benchforge.production-stages/v1' &&
        !retained && receipt.review_revision?.kind!=='user_edit' && !runFinished(view)) {
      // 上一次运行全部完成后，新计划可以开始下一批生产。
      // 已进入 S2–S5 的历史执行不能被普通计划回执或伪造 new_task 倒退到 S1。
      return patch(next,event,{state:'needs_repair',error:'已有生产阶段不可由新计划回执重置；修复当前阶段并续跑原运行。',
        ...(view.review?.stage==='S1'?{review:null}:{})});
    }
    // A slower status/run response can predate a newly committed review notice.
    // Do not let that receipt restore its stale state, error or stage counters.
    if(!retained && snapshot && !currentReviewSnapshot(view.review,snapshot,{allowNewReview:true})) return next;
    const current = Object.values(receipt.steps || {}).find(row=>row.state!=='completed');
    const changes = {workspace:call.workspace,active:activated || view.active};
    if (receipt.error) {next.errorTool=call.name;next.errorCallSeq=call.seq;changes.error=String(receipt.error).slice(0,700);}
    else if (state.errorTool===call.name && call.seq>state.errorCallSeq &&
        !['failed','error','rejected'].includes(receipt.state || receipt.status)) {
      next.errorTool='';changes.error='';
    }
    if(retained) return patch({...next,planReview:snapshot},event,{...changes,workflowTurn:view.turn,plan:receipt.plan});
    // 同一运行的状态查询确认完成时，与续跑回执同样推进到已完成。
    const runCompleted = call.name==='benchforge_pipeline_status' && view.state!=='completed' && receipt.state==='completed' && Boolean(view.run) && receipt.run===view.run;
    const workflow = call.name==='benchforge_pipeline_plan'||call.name==='benchforge_pipeline_run'||call.name==='benchforge_stage_review'||runCompleted;
    if(!workflow) {
      if (view.active && view.stage==='S1' && view.review?.stage==='S1' &&
          ['elapsed','continued'].includes(view.review.state) && call.name==='benchforge_scene_preflight' &&
          receipt.schema==='benchforge.scene-diagnostics/v1') {
        Object.assign(changes,{stage:'S2',state:'running',workflowTurn:view.turn,review:null});
      }
      const watchedSubmission=captureProducers.has(call.name) && trackedSubmission(receipt);
      if(view.active && typeof receipt.run_id==='string' && (watchedSubmission || (call.name==='benchforge_wait' && backgroundWaitRun(receipt)))) {
        if(!view.pendingCaptures.some(row=>row.run===receipt.run_id))
          changes.pendingCaptures=[...view.pendingCaptures,{run:receipt.run_id,name:call.scene || receipt.task_id || receipt.run_id}];
        changes.workflowTurn=view.turn;
      }
      if((['benchforge_scene_status','benchforge_wait'].includes(call.name) && terminalCapture(receipt)) ||
        (call.name==='benchforge_cancel' && receipt.cancel_requested===true)) {
        changes.pendingCaptures=view.pendingCaptures.filter(row=>row.run!==receipt.run_id);
        if(changes.pendingCaptures.length!==view.pendingCaptures.length)changes.workflowTurn=view.turn;
      }
      if(view.active && view.state!=='completed' && captureProducers.has(call.name) && receipt.run_id) {
        next.captureRun=receipt.run_id;
        Object.assign(changes,{workflowTurn:view.turn,state:'waiting_capture',stage:view.stage==='S1'?'S2':view.stage,review:null,capture_budget:null});
        if(receipt.watch?.accepted===false) Object.assign(changes,{state:'capture_error',error:receipt.watch.error || '后台跟踪尚未登记，请用运行编号恢复跟踪。'});
      }
      if(view.active && call.name==='benchforge_scene_blocker' && receipt.state==='blocked') {
        next.errorTool=call.name;next.errorCallSeq=call.seq;
        Object.assign(changes,{workflowTurn:view.turn,state:'blocked',error:receipt.reason});
      }
      return patch(next,event,changes);
    }
    changes.workflowTurn=view.turn;
    if(!view.plan&&call.plan)changes.plan=call.plan;
    if(receipt.plan && typeof receipt.plan==='string') changes.plan = receipt.plan;
    if(receipt.run && typeof receipt.run==='string') changes.run = receipt.run;
    if(receipt.state) changes.state=receipt.state;
    if(['completed','needs_evidence','needs_input','failed','needs_plan_revision'].includes(receipt.state)) changes.review=null;
    if(receipt.stage) changes.stage=receipt.stage;
    else if(current?.result?.stage) changes.stage=current.result.stage;
    if(receipt.steps) changes.completed=Object.values(receipt.steps).filter(row=>row.state==='completed').length;
    if(receipt.total_steps) changes.total=receipt.total_steps;
    if(review?.review_id) {
      if(snapshot){changes.review=snapshot;changes.stage=snapshot.stage;
        if(call.name==='benchforge_stage_review' && ['elapsed','continued'].includes(snapshot.state))changes.state='running';}
    }
    // 运行完成或进入新阶段后，旧阶段的报错不再代表当前缺项。
    if(!receipt.error && (changes.state==='completed' || (changes.stage && changes.stage!==view.stage))) {
      next.errorTool='';changes.error='';
    }
    if(activated && receipt.state==='planned') {
      next.planReview=null;
      delete next.captureRun;
      if(receipt.review_revision?.kind==='new_task') changes.pendingCaptures=[];
      Object.assign(changes,{run:'',completed:0,total:receipt.total_steps || 4,delivered:false,capture_budget:null});
    }
    if((call.name==='benchforge_pipeline_run' || runCompleted) && receipt.state==='completed') changes.pendingCaptures=[];
    if (/^S[3-5]$/.test(changes.stage || '')) changes.capture_budget=null;
    return patch(next,event,changes);
  },
  wire:{viewSchema:ProductionView,/** 返回供界面消费的生产投影视图。 */ view:state=>state.view},
};
