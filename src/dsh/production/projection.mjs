import {z} from 'zod';
import {canonicalToolName} from '../tools/invocation/identifiers.mjs';

export const PRODUCTION_PROJECTION = 'benchforgeProduction';
const Review = z.object({review_id:z.number(), stage:z.string(), artifact:z.string(), deadline:z.number(), state:z.string()});
export const ProductionView = z.object({
  revision:z.number(), active:z.boolean(), running:z.boolean(), turn:z.number(), workflowTurn:z.number(),
  startedAt:z.number(), lastTextAt:z.number(), lastTextSeq:z.number(), lastEventAt:z.number(),
  lastTool:z.string(), tools:z.number(), shells:z.number(), models:z.array(z.string()),
  workspace:z.string(), plan:z.string(), run:z.string(), state:z.string(), stage:z.string(),
  error:z.string(), review:Review.nullable(), completed:z.number(), total:z.number(), delivered:z.boolean(),
});
const State = z.object({view:ProductionView, notices:z.array(z.string()), interruption:z.string(), interruptionSeq:z.number(),
  calls:z.record(z.string(), z.object({name:z.string(), workspace:z.string(), plan:z.string()}))});
const visible = message => (message?.content || []).filter(block => block.type === 'text').map(block => block.text).join('\n').trim();
const parse = text => {try {return JSON.parse(text);} catch {return null;}};
const modelTools = new Set(['benchforge_diffusion','benchforge_scene_sam3','benchforge_depth','benchforge_trellis','benchforge_isaac_sim']);
const patch = (state, event, changes) => ({...state, view:{...state.view, revision:event.seq, lastEventAt:event.time || 0, ...changes}});

export const productionProjection = {
  key:PRODUCTION_PROJECTION, stateVersion:3, stateSchema:State,
  init:header => ({calls:{},notices:[],interruption:'',interruptionSeq:0,view:{revision:0,active:false,running:false,turn:0,workflowTurn:0,startedAt:0,lastTextAt:0,lastTextSeq:-1,
    lastEventAt:0,lastTool:'',tools:0,shells:0,models:[],workspace:header.cwd || '',plan:'',run:'',state:'idle',stage:'',
    error:'',review:null,completed:0,total:0,delivered:false}}),
  apply(state,event) {
    const {view} = state;
    if(event.type === 'turn/start') return patch({...state,interruption:'',interruptionSeq:0},event,{running:true,turn:event.data.turn,startedAt:event.time,lastTextAt:event.time,lastTextSeq:-1,delivered:false});
    if(event.type === 'turn/end') return patch({...state,interruption:event.data.reason?.reason?.kind || '',interruptionSeq:event.seq},event,{running:false,
      ...(event.data.reason?.kind === 'aborted' ? {state:'interrupted'} : {})});
    if(event.type === 'user/message' && event.data.source?.kind === 'benchforge/production-resume') {
      const key=event.data.source.noticeKey;
      return typeof key==='string'&&!state.notices.includes(key)
        ? {...state,notices:[...state.notices.slice(-63),key]} : state;
    }
    if(event.type === 'assistant/message' && visible(event.data.message))
      return patch(state,event,{lastTextAt:event.time,lastTextSeq:event.seq});
    if(event.type === 'deliverables/presented') return patch(state,event,{delivered:true});
    if(event.type === 'tool/call') {
      const {callId} = event.data, name = canonicalToolName(event.data.name), args = parse(event.data.arguments) || {};
      if(!name.startsWith('benchforge_')&&!view.active)return state;
      const next = {...state,calls:{...state.calls,[callId]:{name,workspace:args.workspace || view.workspace,plan:args.plan || ''}}};
      return patch(next,event,{lastTool:name,tools:view.tools+1,shells:view.shells+(name==='bash'?1:0),
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
    const text = visible(message), receipt = parse(text);
    if(message?.isError || event.data.error) return patch(next,event,{error:text.slice(0,700)});
    if(!receipt || typeof receipt !== 'object') return next;
    const activated = receipt.production_contract === 'benchforge.production-stages/v1';
    const review = receipt.stage_review || (call.name==='benchforge_stage_review'?receipt:null);
    const current = Object.values(receipt.steps || {}).find(row=>row.state!=='completed');
    const changes = {workspace:call.workspace,active:activated || view.active};
    const workflow = call.name==='benchforge_pipeline_plan'||call.name==='benchforge_pipeline_run'||call.name==='benchforge_stage_review';
    if(!workflow)return patch(next,event,changes);
    changes.workflowTurn=view.turn;
    changes.error=receipt.error || '';
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
      changes.review={review_id:review.review_id,stage:review.stage,artifact:review.artifact,deadline:review.deadline,state:review.state};
      changes.stage=review.stage;
    }
    if(activated && receipt.state==='planned') Object.assign(changes,{run:'',completed:0,total:receipt.total_steps || 4,delivered:false});
    return patch(next,event,changes);
  },
  wire:{viewSchema:ProductionView,view:state=>state.view},
};
