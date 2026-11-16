import fs from 'node:fs';
import path from 'node:path';
import z from '@deepseek-ai/schemastery';
import { defineTool } from '@deepseek-ai/dsh-tools';
import { createUserMessage } from '@deepseek-ai/dsh-llm';
import { launchEnvironmentOf } from '@deepseek-ai/dsh-launch-environment';
import {createServiceClient, loadServiceConfig} from './service_client.mjs';
import {executeHandoff, handoffParameters} from '../../host/subagent_handoff.mjs';
import {objectOutput, preflightOutput, runOutput, taskRunOutput, observationOutput, taskOutput, cancellationOutput} from './contracts.mjs';
import {createDeliveryReview, createCaptureReviewStep, productionReviewApplies} from './capture/quality_review.mjs';
import {createDeliveryVisuals} from './capture/review_images.mjs';
import {compactCaptureObservation, captureRunId} from './capture/capture_watch.mjs';
import {requestKeyFor} from './request_identity.mjs';
import {trackCaptureSubmission} from './capture/submission.mjs';
export const name='benchforge-scene-tools';
// Delivery visual feedback resolves the active model route and stores image
// attachments while the turn-stopping hook is running. Declare both services
// so the hook remains valid after a normal final response/present boundary.
export const inject=['tools','llm','attachments','benchforgeCapture'];
export const Config=z.object({
  serviceConfig:z.string().required().description('Path to the BenchForge service JSON configuration.'),
});
export async function apply(ctx, config) {
  const environment = launchEnvironmentOf(ctx);
  const loaded = await loadServiceConfig(config.serviceConfig, {
    getEnvironmentValue: (name) => environment.getFrom(name, ['process','project-env','user-env']),
  });
  const {config: cfg} = loaded;
  const post = createServiceClient(loaded);
const withExecutionMode = (value, mode) => {
  if (value.execution_mode !== undefined && value.execution_mode !== mode)
    throw new Error(`execution_mode ${value.execution_mode} conflicts with configured ${mode} mode`);
  return {...value, execution_mode: mode};
};
const output=objectOutput;
async function api(path,args,signal){
  const contextual = {...args};
  if (path === '/start' && Array.isArray(contextual.intents)) {
    contextual.intents = contextual.intents.map(intent => withExecutionMode(intent, loaded.executionMode));
  } else if (['/capture','/refine','/extend','/retry','/scene-preflight'].includes(path)) {
    Object.assign(contextual, withExecutionMode(contextual, loaded.executionMode));
  }
  return post(path, contextual, signal);
}
const tracking=ctx.benchforgeCapture.continuation;
async function observation(path,args,signal){
  const {cursor:ignored,...request}=args;
  request.run_id=captureRunId(request.run_id);
  const {cursor,...value}=await api(path,request,signal);
  return args.detail === true ? value : compactCaptureObservation(value, {artifacts:cfg.artifacts});
}
async function submit(path,args,exec){
  const request_key=requestKeyFor(args,exec);
  const receipt=await api(path,{...args,request_key},exec.signal);
  return trackCaptureSubmission(receipt,path,args,exec,tracking);
}
const requestKey={type:'string',description:'Optional key to reuse the same request across separate tool calls. Omit to use this session and tool-call identity.'};
const layoutInput={type:'object',additionalProperties:false,properties:{mode:{type:'string',enum:['generate','edit','reference'],required:true},prompt:{type:'string',description:'Visual description for generate/edit.'},source_image:{type:'string'},source_view:{type:'integer'},seed:{type:'integer'},design_task_id:{type:'string',description:'For mode=reference only: completed benchforge_layout task ID. Preserves diffusion provenance and avoids regeneration; no other source fields.'}}};
const programInput={type:'string',description:'Complete SceneProgram JSON in the task workspace, written with standard file tools or task_code; the initial pass uses this snapshot directly.'};
  async function imageAttachment(imagePath,name,signal){
    signal.throwIfAborted();
    const data=await fs.promises.readFile(imagePath,{signal});
    signal.throwIfAborted();
    const attachment=await ctx.attachments.saveImage({data,mediaType:/\.jpe?g$/i.test(imagePath)?'image/jpeg':'image/png',name});
    signal.throwIfAborted();
    return attachment;
  }
  const visuals=createDeliveryVisuals({api,images:async(paths,agent,signal)=>{
    const routed=agent.session.requestHeader()?.config;
    const info=await ctx.llm.resolveModelInfo(routed?.provider??agent.options.provider,routed?.model??agent.options.model,signal);
    if(!info.inputModalities?.includes('image'))return [];
    const blocks=[];
    for(const imagePath of paths)blocks.push({type:'image',attachment:await imageAttachment(imagePath,path.basename(imagePath),signal)});
    return blocks;
  }});
  const review=createDeliveryReview({api,createMessage:createUserMessage,visuals,
    shouldReview:agent=>productionReviewApplies(agent,agent.ctx?.get('tools') || ctx.tools)});
  ctx.on('agent/pre-step',createCaptureReviewStep(review));
  ctx.on('agent/turn-stopping',input=>review(input));
  ctx.tools.register(defineTool({name:'benchforge_scene_collaboration',
    description:'Share lightweight handoffs in the task workspace. Publish observations, artifacts, decisions, blockers and next actions; relate or reply to earlier handoffs; list or read records when agents join, resume or reconcile parallel work. The protocol does not prescribe a fixed orchestration graph.',
    parameters:{workspace:{type:'string',required:true,description:'Absolute shared task workspace.'},...handoffParameters},
    output,execute:executeHandoff}));
  const stage=(name,path,description,parameters)=>ctx.tools.register(defineTool({name,description,parameters:{request_key:requestKey,...parameters},output:taskRunOutput,execute:(args,exec)=>submit(path,args,exec)}));
  stage('benchforge_diffusion','/diffusion','Generate or edit an image with FLUX. Takes your prompt directly and returns a task with image evidence. Use the returned source_path with segmentation, reconstruction or scene authoring when those steps are useful.',{
    prompt:{type:'string',required:true},source_image:{type:'string',description:'Optional input image source_path for editing.'},width:{type:'integer'},height:{type:'integer'},steps:{type:'integer'},seed:{type:'integer'}});
  stage('benchforge_scene_sam3','/sam3','Segment an image with SAM3. Returns candidate masks, boxes and scores for inspection and selection. Prompts use text; instances use pixel xyxy boxes. Use the selected evidence for downstream reconstruction.',{
    source_image:{type:'string',required:true},prompts:{type:'array',items:{type:'string'}},instances:{type:'array',items:{type:'object',additionalProperties:true,properties:{id:{type:'integer'},label:{type:'string'},box:{type:'array',items:{type:'number'}},segmentation_prompt:{type:'string'}}}},confidence:{type:'number'}});
  stage('benchforge_trellis','/trellis','Generate a Y-up PBR mesh per selected object mask with FastTrellis2. Register a selected mesh with benchforge_mesh_import. Set physical size when placing it in a scene.',{
    source_image:{type:'string',required:true},objects:{type:'array',required:true,items:{type:'object',additionalProperties:false,properties:{object_id:{type:'string',required:true},source_mask:{type:'string',required:true},seed:{type:'integer'}}}},seed:{type:'integer'}});
  const cameraMatrix={type:'array',items:{type:'array',items:{type:'number'}}};
  stage('benchforge_depth','/depth','Estimate single-image depth in meters with FastDA3. Returns a depth array, visualization and camera parameters. For optional pose conditioning supply both intrinsics and world-to-camera extrinsics.',{
    source_image:{type:'string',required:true},input_size:{type:'integer',description:'Provider processing resolution, 140–1024.'},
    intrinsics:{...cameraMatrix,description:'3x3 camera intrinsic matrix.'},extrinsics:{...cameraMatrix,description:'3x4 or 4x4 world-to-camera matrix.'}});
  stage('benchforge_mesh_import','/mesh-import','Convert and register an existing mesh for SceneProgram use without model inference or visual review. Returns asset_id and bounds. Preserve the generator-declared source_frame: TRELLIS.2 GLB uses y_up; use sam3d_camera for preexisting camera-frame assets. An existing paired Gaussian sidecar is imported automatically; source_gaussian can supply an explicit paired NPZ.',{
    source_mesh:{type:'string',required:true},source_gaussian:{type:'string',description:'Paired activated Gaussian NPZ; coordinates match source_mesh or include local_to_source.'},label:{type:'string',required:true},source_up_axis:{type:'string',enum:['Y','Z']},source_frame:{type:'string',enum:['sam3d_camera','y_up','z_up']},size_hint_m:{type:'array',items:{type:'number'}}});
  stage('benchforge_capture','/capture','Render and simulate your complete SceneProgram on the Isaac Sim A100 gRPC service. Returns actual images and native geometry receipts. Read the live catalog for explicit robot/world commands. With strict scene policy enabled, physical and per-view life acceptance is mandatory even for capture-only; inspect scene_admission.json and scene_acceptance.json. A captured frame is not completion. Failed acceptance cannot export and needs a revised complete program through capture/refine. Does not automatically revise your program or export a dataset. When matching a separately generated diffusion reference, link that exact image with layout={mode:"reference",source_image:<returned source_path>} so the reference accompanies the renders at delivery review; reuse it without regenerating.',{
    scene_program_path:{type:'string',required:true},name:{type:'string'},layout:layoutInput,
    capture_options:{type:'object',description:'Optional output selection for local iteration; defaults to every view and a USD export. Keeps the full program, physics and action recordings.',additionalProperties:false,properties:{view_indices:{type:'array',items:{type:'integer'},description:'Zero-based program camera indices. Original view_N filenames are retained. [] skips final scene views.'},export_scene:{type:'boolean',description:'Set false to skip USD/resource export for this capture; default true.'}}}});
  ctx.tools.register(defineTool({name:'benchforge_preview',description:'Compute placement bounds, support gaps and geometry advice from your SceneProgram without starting Isaac or a model. Use capture for actual images and physics.',parameters:{scene_program_path:{type:'string',required:true}},output,execute:(args,exec)=>api('/preview',args,exec.signal)}));
  ctx.tools.register(defineTool({
    name:'benchforge_scene_preflight',
    description:'Batch-check a complete SceneProgram before capture or a repair submission. Returns all independent contract, asset, plan and geometry diagnostics, related repair groups, skipped checks and an immutable full report with its input snapshot. Read the full issues and combine them with existing native/visual evidence to repair related defects in one candidate. Warnings are hypotheses, not proof of collision or wrong pose; pending/skipped checks are not passes. Preserves input and hard requirements, starts no simulation or generation, and does not certify scene acceptance.',
    parameters:{scene_program_path:{type:'string',required:true,description:'Complete JSON under the configured task workspace.'}},
    output:preflightOutput,
    isConcurrencySafe:()=>true,
    execute:(args,exec)=>api('/scene-preflight',args,exec.signal),
  }));
  ctx.tools.register(defineTool({name:'benchforge_layout',description:"Generate, edit or reuse a diffusion design reference. Optional standalone tool; run can prepare the reference internally. Read layout/reference.png with evidence, and reuse via layout={mode:\"reference\",design_task_id}. This produces a design image, not a simulated scene.",parameters:{request_key:requestKey,name:{type:'string',required:true},description:{type:'string',required:true},layout:layoutInput},output:runOutput,execute:(args,exec)=>submit('/layout',args,exec)}));
  ctx.tools.register(defineTool({name:'benchforge_scene_evidence',description:"Read actual JSON, images and action traces from current or earlier revisions, including failed attempts. Omit file to list evidence. Use workspace_id to copy a file for task-code processing. Production evidence is read-only; authority GT is excluded.",parameters:{task_id:{type:'string',required:true},revision:{type:'integer'},file:{type:'string'},workspace_id:{type:'string'}},output:{...taskOutput,render:(_args,value)=>[{type:'text',text:JSON.stringify({...value,image:undefined})},...(value.image?[{type:'image',attachment:value.image}]:[])]},execute:async(args,exec)=>{
    const value=await api('/evidence',args,exec.signal);
    if(value.image_path){
      const attachments=ctx.get('attachments');const llm=ctx.get('llm');
      const routed=exec.agent?.session.requestHeader()?.config;
      const provider=routed?.provider??exec.agent?.options.provider;const model=routed?.model??exec.agent?.options.model;
      if(!attachments||!llm||provider===undefined||model===undefined)throw Error('Image evidence needs the attachment service and a resolved vision route; copy to workspace_id for file processing.');
      const info=await llm.resolveModelInfo(provider,model,exec.signal);
      if(!info.inputModalities?.includes('image'))throw Error('Current model route does not declare image input; use JSON evidence or copy the image to workspace_id.');
      value.image=await imageAttachment(value.image_path,path.basename(value.image_path),exec.signal);
      delete value.image_path;
    }
    return value;
  }}));
  ctx.tools.register(defineTool({name:'benchforge_scene_catalog',description:"Discover tool capabilities, SceneProgram format and assets. Choose a section; query filters, item_id selects detail, next_offset continues. workspace_id copies all matches for task-code inspection. scene_schema fragments concatenate into the complete format.",parameters:{section:{type:'string',enum:['overview','contracts','scene_schema','native_assets','generated_assets','generation_tools','materials','environments']},query:{type:'string'},item_id:{type:'string'},offset:{type:'integer'},limit:{type:'integer'},workspace_id:{type:'string'}},output,execute:(args,exec)=>api('/catalog',args,exec.signal)}));
  ctx.tools.register(defineTool({name:'benchforge_run',description:"Produce a scene and dataset from natural-language requirements or scene_program_path. The pipeline prepares a diffusion reference unless one is supplied, then builds, captures, reviews and exports through Isaac Sim A100. Returns a durable run ID.",parameters:{request_key:requestKey,intents:{type:'array',required:true,items:{type:'object',additionalProperties:false,properties:{name:{type:'string',required:true},description:{type:'string',required:true},split:{type:'string',enum:['train','dev'],required:true},target_items:{type:'integer',description:'Number of dataset questions to export; this is not the scene entity count.'},layout:layoutInput,scene_program_path:programInput}}}},output:runOutput,execute:(args,exec)=>submit('/start',args,exec)}));
  ctx.tools.register(defineTool({name:'benchforge_asset',description:"Generate with diffusion, SAM3 and the configured reconstruction tool, reconstruct source_image, import source_mesh, or reuse a catalog asset_id. Inputs use registered source paths. Returns a durable run ID.",parameters:{request_key:requestKey,intent:{type:'object',required:true,additionalProperties:false,properties:{name:{type:'string',required:true},description:{type:'string',required:true},label:{type:'string',description:'Semantic subject for SAM3 segmentation and asset registration; defaults to name.'},source_image:{type:'string'},source_mask:{type:'string'},source_mesh:{type:'string'},source_gaussian:{type:'string'},asset_id:{type:'string'},seed:{type:'integer'},source_up_axis:{type:'string',enum:['Y','Z']},size_hint_m:{type:'array',items:{type:'number'}},subject_box:{type:'array',items:{type:'number'},description:'Pixel xyxy box selecting the subject.'},edit_reference:{type:'boolean'}}}},output:runOutput,execute:(args,exec)=>submit('/asset',args,exec)}));
  ctx.tools.register(defineTool({name:'benchforge_extend',description:"Add objects to an accepted captured scene while retaining its existing entities and train/dev family. With strict scene policy, provide a complete scene_program_path that preserves all parent objects unchanged and includes the saved scene brief.",parameters:{request_key:requestKey,parent_task_id:{type:'string',required:true},name:{type:'string',required:true},description:{type:'string',required:true},target_items:{type:'integer',description:'Number of dataset questions to export; this is not the scene entity count.'},layout:layoutInput,scene_program_path:programInput},output:runOutput,execute:(args,exec)=>submit('/extend',args,exec)}));
  ctx.tools.register(defineTool({name:'benchforge_retry',description:"Recover a failed stage without a new scene version. Reuse retained native requests and downloads after interruption; only confirmed terminal failures start a new attempt. Quality failures still require refine/capture.",parameters:{task_id:{type:'string',required:true},feedback:{type:'string',description:'Optional concrete scene repair guidance for a failed planning stage, up to 12000 characters.'},capture_mode:{type:'string',enum:['auto','resume','restart'],description:'auto resumes retained native jobs unless they failed terminally. resume preserves request IDs and partial downloads. restart creates a new attempt only after prior jobs are confirmed terminal.'}},output:taskOutput,execute:(args,exec)=>submit('/retry',args,exec)}));
  ctx.tools.register(defineTool({name:'benchforge_scene_status',description:"Read a concise current stage, real failures, quality outcomes and evidence/receipt paths. run_id excludes .sceneN. Use detail=true when the full saved SceneProgram or task reports are needed; ordinary progress checks use the default summary. Lifecycle completion and scene quality are separate fields.",parameters:{run_id:{type:'string',required:true},detail:{type:'boolean'}},output:observationOutput,execute:(args,exec)=>observation(args.detail?'/inspect':'/observe',args,exec.signal)}));
  ctx.tools.register(defineTool({name:'benchforge_wait',description:"Wait for capture progress. Scene submissions are tracked automatically by the host. Use background=true to recover tracking or attach an existing run: returns immediately and wakes this session on terminal state or observer failure, without model polling. Continue independent work or finish this turn while waiting. Synchronous waits remain bounded at 1..60 seconds. Read benchforge_scene_status with detail=true for full saved reports.",parameters:{run_id:{type:'string',required:true},background:{type:'boolean',description:'Register or recover one background observer for this session/run; no repeated wait calls are needed.'},timeout_seconds:{type:'integer',description:'Synchronous wait only, 1..60 seconds.'}},output:observationOutput,execute:async(args,exec)=>{
    const {background,...request}=args;
    if(background){
      await tracking.register(exec.agent,request,{resume:true});
      return {...await observation('/observe',request,exec.signal),watch:{background:true,accepted:true}};
    }
    return observation('/wait',request,exec.signal);
  }}));
  ctx.tools.register(defineTool({name:'benchforge_scene_blocker',description:'Record a concrete external blocker or two consecutive captures with the same unresolved failure. Requires a settled task owned by this session, saved service evidence, the specific reason and the missing condition. This never approves failed scenes. Ordinary quality defects must first be repaired; a pending capture already has automatic tracking.',
    parameters:{run_id:{type:'string',required:true},kind:{type:'string',enum:['external','no_progress'],required:true},
      reason:{type:'string',required:true},missing_condition:{type:'string',required:true},evidence:{type:'array',items:{type:'string'},required:true}},
    output,execute:(args,exec)=>tracking.block(exec.agent,args)}));
  ctx.tools.register(defineTool({name:'benchforge_task_code',description:"Run Python task code in a persistent writable /workspace. Read-only modules are under /harness and reusable assets under /assets. Use it for calculations, image/mesh processing, diagnostics and complete SceneProgram JSON. Submit returned source_path through production tools. Timeout 1..300 seconds, default 60; code up to 1 MiB.",parameters:{workspace_id:{type:'string',required:true},code:{type:'string',required:true},timeout_seconds:{type:'integer'}},output,execute:(args,exec)=>api('/task-code',args,exec.signal)}));
  ctx.tools.register(defineTool({name:'benchforge_refine',description:"Revise a captured scene from feedback or a complete SceneProgram. Can change layout, objects, materials, lights, cameras and actions. A capture-only parent stays capture-only unless target_items is explicitly supplied; otherwise the parent data target is inherited. source_revision optionally selects an earlier capture; preserves the source and train/dev family.",parameters:{request_key:requestKey,parent_task_id:{type:'string',required:true},source_revision:{type:'integer',description:'Optional captured revision to refine; use evidence to choose an earlier capture when the latest planning attempt failed.'},name:{type:'string',required:true},description:{type:'string',required:true},target_items:{type:'integer',description:'Number of dataset questions to export; this is not the scene entity count.'},layout:layoutInput,scene_program_path:programInput},output:taskRunOutput,execute:(args,exec)=>submit('/refine',args,exec)}));
  ctx.tools.register(defineTool({name:'benchforge_cancel',description:"Cancel this run, preserving its artifacts and shared services.",parameters:{run_id:{type:'string',required:true}},output:cancellationOutput,execute:(args,exec)=>api('/cancel',args,exec.signal)}));
}
