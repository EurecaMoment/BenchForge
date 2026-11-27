import {saveSceneImage} from './capture/repair_evidence.mjs';
import z from '@deepseek-ai/schemastery';
import { defineTool } from '@deepseek-ai/dsh-tools';
import { createUserMessage } from '@deepseek-ai/dsh-llm';
import { launchEnvironmentOf } from '@deepseek-ai/dsh-launch-environment';
import {createServiceClient, loadServiceConfig} from './service_client.mjs';
import {executeHandoff, handoffParameters} from '../../host/subagent_handoff.mjs';
import {objectOutput, preflightOutput, renderPreflight, renderEvidence, runOutput, taskRunOutput, observationOutput, taskOutput, cancellationOutput} from './contracts.mjs';
import {createDeliveryReview, createCaptureReviewStep, productionReviewApplies} from './capture/quality_review.mjs';
import {createDeliveryVisuals} from './capture/review_images.mjs';
import {compactCaptureObservation, captureRunId} from './capture/capture_watch.mjs';
import {requestKeyFor} from './request_identity.mjs';
import {trackCaptureSubmission} from './capture/submission.mjs';
import {DEFAULT_REPORT_MAX_CHARS, reportViewParameters, splitReportRequest} from './report_view.mjs';
export const name='benchforge-scene-tools';
// Delivery visual feedback resolves the active model route and stores image
// attachments while the turn-stopping hook is running. Declare both services
// so the hook remains valid after a normal final response/present boundary.
export const inject=['tools','llm','attachments','benchforgeCapture'];
export const Config=z.object({
  serviceConfig:z.string().required().description('Path to the BenchForge service JSON configuration.'),
  reportMaxChars:z.number().step(1).min(4000).default(DEFAULT_REPORT_MAX_CHARS),
});
/** 加载可信服务配置并注册场景、采集与复核工具，统一执行模式、图片附件和后台追踪。 */
export async function apply(ctx, config) {
  const environment = launchEnvironmentOf(ctx);
  const loaded = await loadServiceConfig(config.serviceConfig, {
    /** 从宿主允许的环境来源解析服务配置中的凭据引用。 */
    getEnvironmentValue: (name) => environment.getFrom(name, ['process','project-env','user-env']),
  });
  const {config: cfg} = loaded;
  const post = createServiceClient(loaded);
/** 为业务请求补上已配置的执行模式，并拒绝与显式请求冲突的模式。 */
const withExecutionMode = (value, mode) => {
  if (value.execution_mode !== undefined && value.execution_mode !== mode)
    throw new Error(`execution_mode ${value.execution_mode} conflicts with configured ${mode} mode`);
  return {...value, execution_mode: mode};
};
const output=objectOutput;
/** 按接口类型为场景或批量意图补充执行模式，然后发送可信服务请求。 */
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
/** 规范采集运行标识并读取状态；未请求详情时仅返回精简观察摘要。 */
async function observation(path,args,signal){
  const {cursor:ignored,...request}=args;
  request.run_id=captureRunId(request.run_id);
  const {cursor,...value}=await api(path,request,signal);
  return args.detail === true ? value : compactCaptureObservation(value, {artifacts:cfg.artifacts});
}
/** 给写入请求补充稳定幂等键，并登记成功提交的采集任务供后台续接。 */
async function submit(path,args,exec){
  const request_key=requestKeyFor(args,exec);
  const receipt=await api(path,{...args,request_key},exec.signal);
  return trackCaptureSubmission(receipt,path,args,exec,tracking);
}
const requestKey={type:'string',description:'Optional key to reuse the same request across separate tool calls. Omit to use this session and tool-call identity.'};
const layoutInput={type:'object',additionalProperties:false,properties:{mode:{type:'string',enum:['generate','edit','reference'],required:true},prompt:{type:'string',description:'Visual description for generate/edit.'},source_image:{type:'string'},source_view:{type:'integer'},seed:{type:'integer'},design_task_id:{type:'string',description:'For mode=reference only: completed benchforge_layout task ID. Reuses its reference image and layout estimate.'},objects:{type:'array',description:'Expected visible object inventory for image-derived placement. Ordinary standalone objects default to FastTrellis. Set requires_articulation only when the requested task needs joints, actuation, kinematics, grasping or locomotion. Static robot, drone and vehicle shells remain TRELLIS objects.',items:{type:'object',additionalProperties:true,properties:{object_id:{type:'string',required:true},label:{type:'string',required:true},segmentation_prompt:{type:'string'},count:{type:'integer',description:'Expected visible instance count, default 1.'},representation:{type:'string',enum:['auto','trellis','native'],description:'Legacy hint. Ordinary objects still default to FastTrellis; fixed architecture and explicitly articulated bodies are classified by the backend.'},requires_articulation:{type:'boolean',description:'True only when this object must perform articulated or controlled motion.'},seed:{type:'integer'}}}},camera:{type:'object',description:'Optional scene camera used to project predicted depth. When provided, position and target are required. Omission records an explicit virtual-camera assumption.',additionalProperties:false,properties:{position:{type:'array',required:true,items:{type:'number'}},target:{type:'array',required:true,items:{type:'number'}},up:{type:'array',items:{type:'number'}},focal_length_mm:{type:'number'},horizontal_aperture_mm:{type:'number'}}}}};
const programInput={type:'string',description:'Complete SceneProgram JSON in the task workspace, written with standard file tools or task_code; the initial pass uses this snapshot directly.'};
  const visuals=createDeliveryVisuals({api,/** 仅向支持图片输入的当前模型生成图片块，并逐张保存为宿主附件。 */ images:async(paths,agent,signal)=>{
    const routed=agent.session.requestHeader()?.config;
    const info=await ctx.llm.resolveModelInfo(routed?.provider??agent.options.provider,routed?.model??agent.options.model,signal);
    if(!info.inputModalities?.includes('image'))return [];
    const blocks=[];
    for(const imagePath of paths)blocks.push({type:'image',attachment:await saveSceneImage(ctx.attachments,imagePath,signal)});
    return blocks;
  }});
  const review=createDeliveryReview({api,createMessage:createUserMessage,visuals,
    /** 根据当前 Agent 可见的生产工具判断其是否承担交付复核责任。 */
    shouldReview:agent=>productionReviewApplies(agent,agent.ctx?.get('tools') || ctx.tools)});
  ctx.on('agent/pre-step',createCaptureReviewStep(review));
  ctx.on('agent/turn-stopping',input=>review(input));
  ctx.tools.register(defineTool({name:'benchforge_scene_collaboration',
    description:'Share lightweight handoffs in the task workspace. Publish observations, artifacts, decisions, blockers and next actions; relate or reply to earlier handoffs; list or read records when agents join, resume or reconcile parallel work. The protocol does not prescribe a fixed orchestration graph.',
    parameters:{workspace:{type:'string',required:true,description:'Absolute shared task workspace.'},...handoffParameters},
    output,execute:executeHandoff}));
  /** 使用统一请求键和结果结构注册一个会创建阶段任务的业务工具。 */
  const stage=(name,path,description,parameters)=>ctx.tools.register(defineTool({name,description,parameters:{request_key:requestKey,...parameters},output:taskRunOutput,/** 将阶段工具请求交由统一提交入口处理幂等性和后台追踪。 */ execute:(args,exec)=>submit(path,args,exec)}));
  ctx.tools.register(defineTool({name:'benchforge_records',
    description:'Read persisted PostgreSQL records authorized for this conversation. Fixed resources and bounded pages; no credentials or SQL are exposed. Reading does not approve stages or resume production.',
    parameters:{resource:{type:'string',required:true,enum:['runs','tasks','events','operations','reviews','plans','native_jobs']},
      limit:{type:'integer',description:'1–100, default 30.'},after:{type:'string',description:'Cursor returned by the previous page.'}},
    output,/** 声明该只读工具允许与其他工具并发执行。 */ isConcurrencySafe:()=>true,/** 通过统一服务入口执行 /records 操作，并传递当前调用的取消信号。 */ execute:(args,exec)=>api('/records',args,exec.signal)}));
  stage('benchforge_diffusion','/diffusion','Generate or edit an image with FLUX. Takes your prompt directly and returns a task with image evidence. Use the returned source_path with segmentation, reconstruction or scene authoring when those steps are useful.',{
    prompt:{type:'string',required:true},source_image:{type:'string',description:'Optional input image source_path for editing.'},width:{type:'integer'},height:{type:'integer'},steps:{type:'integer'},seed:{type:'integer'}});
  stage('benchforge_scene_sam3','/sam3','Segment an image with SAM3. Returns candidate masks, boxes and scores for inspection and selection. Prompts use text; instances use pixel xyxy boxes. Use the selected evidence for downstream reconstruction.',{
    source_image:{type:'string',required:true},prompts:{type:'array',items:{type:'string'}},instances:{type:'array',items:{type:'object',additionalProperties:true,properties:{id:{type:'integer'},label:{type:'string'},box:{type:'array',items:{type:'number'}},segmentation_prompt:{type:'string'}}}},confidence:{type:'number'}});
  stage('benchforge_trellis','/trellis','Generate a Y-up PBR mesh per selected object mask with FastTrellis2. Register a selected mesh with benchforge_mesh_import. Set physical size when placing it in a scene.',{
    source_image:{type:'string',required:true},objects:{type:'array',required:true,items:{type:'object',additionalProperties:false,properties:{object_id:{type:'string',required:true},source_mask:{type:'string',required:true},seed:{type:'integer'}}}},seed:{type:'integer'}});
  const cameraMatrix={type:'array',items:{type:'array',items:{type:'number'}}};
  stage('benchforge_depth','/depth','Estimate single-image depth in meters with FastDA3. The service rejects responses without explicit meter units. Returns an estimated_metric_depth array, visualization and camera parameters. For optional pose conditioning supply both intrinsics and world-to-camera extrinsics. Use benchforge_layout when you need the integrated image-to-initial-placement workflow.',{
    source_image:{type:'string',required:true},input_size:{type:'integer',description:'Provider processing resolution, 140–1024.'},
    intrinsics:{...cameraMatrix,description:'3x3 camera intrinsic matrix.'},extrinsics:{...cameraMatrix,description:'3x4 or 4x4 world-to-camera matrix.'}});
  stage('benchforge_mesh_import','/mesh-import','Convert and register an existing static GLB/glTF/OBJ/PLY/STL mesh for SceneProgram use without model inference or visual review. USD and robots use benchforge_isaac_sim upload/import and native_asset; mesh conversion does not preserve articulation. Returns asset_id and bounds. Preserve the generator-declared source_frame: TRELLIS.2 GLB uses y_up; use sam3d_camera for preexisting camera-frame assets. An existing paired Gaussian sidecar is imported automatically; source_gaussian can supply an explicit paired NPZ.',{
    source_mesh:{type:'string',required:true},source_gaussian:{type:'string',description:'Paired activated Gaussian NPZ; coordinates match source_mesh or include local_to_source.'},label:{type:'string',required:true},source_up_axis:{type:'string',enum:['Y','Z']},source_frame:{type:'string',enum:['sam3d_camera','y_up','z_up']},size_hint_m:{type:'array',items:{type:'number'}}});
  stage('benchforge_capture','/capture','Render and simulate your complete SceneProgram on the Isaac Sim A100 gRPC service. Returns actual images and native geometry receipts. Read the live catalog for explicit robot/world commands. With strict scene policy enabled, physical and per-view life acceptance is mandatory even for capture-only; inspect scene_admission.json and scene_acceptance.json. A captured frame is not completion. Failed acceptance cannot export and needs a revised complete program through capture/refine. Does not automatically revise your program or export a dataset. When matching a separately generated diffusion reference, link that exact image with layout={mode:"reference",source_image:<returned source_path>} so the reference accompanies the renders at delivery review; reuse it without regenerating.',{
    scene_program_path:{type:'string',required:true},name:{type:'string'},layout:layoutInput,
    capture_options:{type:'object',description:'Optional output selection for local iteration; defaults to every view and a USD export. Keeps the full program, physics and action recordings.',additionalProperties:false,properties:{view_indices:{type:'array',items:{type:'integer'},description:'Zero-based program camera indices. Original view_N filenames are retained. [] skips final scene views.'},export_scene:{type:'boolean',description:'Set false to skip USD/resource export for this capture; default true.'}}}});
  ctx.tools.register(defineTool({name:'benchforge_preview',description:'Compute placement bounds, support gaps and geometry advice from your SceneProgram without starting Isaac or a model. Use capture for actual images and physics.',parameters:{scene_program_path:{type:'string',required:true}},output,/** 通过统一服务入口执行 /preview 操作，并传递当前调用的取消信号。 */ execute:(args,exec)=>api('/preview',args,exec.signal)}));
  ctx.tools.register(defineTool({
    name:'benchforge_scene_preflight',
    description:'Batch-check a complete SceneProgram before capture or a repair submission. Saves all independent contract, asset, plan and geometry diagnostics, repair groups, skipped checks and an immutable input snapshot. Large reports show an incomplete overview; use returned json_pointer paths and offset/limit to read all issues with this tool, or detail=true for the full selected data. Combine related issues with existing native/visual evidence to repair them in one candidate. Warnings are hypotheses, not proof of collision or wrong pose; pending/skipped checks are not passes. Preserves input and hard requirements, starts no simulation or generation, and does not certify scene acceptance.',
    parameters:{scene_program_path:{type:'string',required:true,description:'Complete JSON under the configured task workspace.'},...reportViewParameters},
    output:{...preflightOutput,render:(args,value)=>renderPreflight(args,value,config.reportMaxChars)},
    /** 声明该只读工具允许与其他工具并发执行。 */
    isConcurrencySafe:()=>true,
    /** 通过统一服务入口执行 /scene-preflight 操作，并传递当前调用的取消信号。 */
    execute:(args,exec)=>api('/scene-preflight',splitReportRequest(args).request,exec.signal),
  }));
  ctx.tools.register(defineTool({name:'benchforge_layout',description:"Create an image-based initial scene layout before authoring the SceneProgram. For a new or changed image, provide the expected visible objects in layout.objects. The backend uses configured FastFLUX for a design image, then FastSAM3 and FastDA3 on that exact image, and FastTrellis for ordinary visible objects, including static robot shells. Fixed architecture and bodies explicitly requiring articulation bypass TRELLIS. For a moving robot, select an Isaac Sim asset with active joints/drives from the live catalog or upload a supported official USD/URDF/MJCF model; a pose-baked model is not controllable. It returns the reference image, segmentation overlay, registered asset IDs and editable estimated positions/sizes. Treat them as starting points, inspect and adjust support, orientation and clearances, then write the final SceneProgram. Wait for completion and read layout_estimate.json. Reuse a completed estimate with layout={mode:\"reference\",design_task_id}.",parameters:{request_key:requestKey,name:{type:'string',required:true},description:{type:'string',required:true},layout:layoutInput},output:runOutput,/** 通过统一服务入口执行 /layout 操作，并传递当前调用的取消信号。 */ execute:(args,exec)=>submit('/layout',args,exec)}));
  ctx.tools.register(defineTool({name:'benchforge_scene_evidence',description:"Read actual JSON, images and action traces, including failed attempts. Omit file to list evidence. Large JSON returns a bounded overview with exact json_pointer paths; query those paths and array pages with this tool rather than parsing spill files in shell. detail=true retrieves full selected JSON. Use workspace_id to copy a file for task-code processing. Evidence is read-only; authority GT is excluded.",parameters:{task_id:{type:'string',required:true},revision:{type:'integer'},file:{type:'string'},workspace_id:{type:'string'},...reportViewParameters},output:{...taskOutput,/** 将结构化结果与可选图片附件组合为工具输出块。 */ render:(args,value)=>renderEvidence(args,value,config.reportMaxChars)},/** 通过统一服务入口执行 /evidence 操作，并传递当前调用的取消信号。 */ execute:async(args,exec)=>{
    const value=await api('/evidence',splitReportRequest(args).request,exec.signal);
    if(value.image_path){
      const attachments=ctx.get('attachments');const llm=ctx.get('llm');
      const routed=exec.agent?.session.requestHeader()?.config;
      const provider=routed?.provider??exec.agent?.options.provider;const model=routed?.model??exec.agent?.options.model;
      if(!attachments||!llm||provider===undefined||model===undefined)throw Error('Image evidence needs the attachment service and a resolved vision route; copy to workspace_id for file processing.');
      const info=await llm.resolveModelInfo(provider,model,exec.signal);
      if(!info.inputModalities?.includes('image'))throw Error('Current model route does not declare image input; use JSON evidence or copy the image to workspace_id.');
      value.image=await saveSceneImage(attachments,value.image_path,exec.signal);
      delete value.image_path;
    }
    return value;
  }}));
  ctx.tools.register(defineTool({name:'benchforge_scene_catalog',description:"Discover tool capabilities, SceneProgram format and assets. Choose a section; query filters, item_id selects detail, next_offset continues. workspace_id copies all matches for task-code inspection. scene_schema fragments concatenate into the complete format.",parameters:{section:{type:'string',enum:['overview','contracts','scene_schema','native_assets','generated_assets','generation_tools','materials','environments']},query:{type:'string'},item_id:{type:'string'},offset:{type:'integer'},limit:{type:'integer'},workspace_id:{type:'string'}},output,/** 通过统一服务入口执行 /catalog 操作，并传递当前调用的取消信号。 */ execute:(args,exec)=>api('/catalog',args,exec.signal)}));
  ctx.tools.register(defineTool({name:'benchforge_run',description:"Produce a scene and dataset from natural-language requirements or scene_program_path. Complete ScenePrograms can be submitted directly. For an image-led design, benchforge_layout can provide initial placement and registered assets. Returns a durable run ID.",parameters:{request_key:requestKey,intents:{type:'array',required:true,items:{type:'object',additionalProperties:false,properties:{name:{type:'string',required:true},description:{type:'string',required:true},split:{type:'string',enum:['train','dev'],required:true},target_items:{type:'integer',description:'Number of dataset questions to export; this is not the scene entity count.'},layout:layoutInput,scene_program_path:programInput}}}},output:runOutput,/** 通过统一服务入口执行 /start 操作，并传递当前调用的取消信号。 */ execute:(args,exec)=>submit('/start',args,exec)}));
  ctx.tools.register(defineTool({name:'benchforge_asset',description:"Generate with diffusion, SAM3 and the configured reconstruction tool, reconstruct source_image, import source_mesh, or reuse a catalog asset_id. Inputs use registered source paths. Returns a durable run ID.",parameters:{request_key:requestKey,intent:{type:'object',required:true,additionalProperties:false,properties:{name:{type:'string',required:true},description:{type:'string',required:true},label:{type:'string',description:'Semantic subject for SAM3 segmentation and asset registration; defaults to name.'},source_image:{type:'string'},source_mask:{type:'string'},source_mesh:{type:'string'},source_gaussian:{type:'string'},asset_id:{type:'string'},seed:{type:'integer'},source_up_axis:{type:'string',enum:['Y','Z']},size_hint_m:{type:'array',items:{type:'number'}},subject_box:{type:'array',items:{type:'number'},description:'Pixel xyxy box selecting the subject.'},edit_reference:{type:'boolean'}}}},output:runOutput,/** 通过统一服务入口执行 /asset 操作，并传递当前调用的取消信号。 */ execute:(args,exec)=>submit('/asset',args,exec)}));
  ctx.tools.register(defineTool({name:'benchforge_extend',description:"Add objects to an accepted captured scene while retaining its existing entities and train/dev family. With strict scene policy, provide a complete scene_program_path that preserves all parent objects unchanged and includes the saved scene brief.",parameters:{request_key:requestKey,parent_task_id:{type:'string',required:true},name:{type:'string',required:true},description:{type:'string',required:true},target_items:{type:'integer',description:'Number of dataset questions to export; this is not the scene entity count.'},layout:layoutInput,scene_program_path:programInput},output:runOutput,/** 通过统一服务入口执行 /extend 操作，并传递当前调用的取消信号。 */ execute:(args,exec)=>submit('/extend',args,exec)}));
  ctx.tools.register(defineTool({name:'benchforge_retry',description:"Recover a failed stage without a new scene version. Reuse retained native requests and downloads after interruption; only confirmed terminal failures start a new attempt. Quality failures still require refine/capture.",parameters:{task_id:{type:'string',required:true},feedback:{type:'string',description:'Optional concrete scene repair guidance for a failed planning stage, up to 12000 characters.'},capture_mode:{type:'string',enum:['auto','resume','restart','review_only'],description:'auto resumes retained jobs. resume preserves their IDs and downloads. restart replaces terminal native jobs. review_only reassesses an eligible archived capture once under the updated acceptance policy, using its original images and physics.'}},output:taskOutput,/** 通过统一服务入口执行 /retry 操作，并传递当前调用的取消信号。 */ execute:(args,exec)=>submit('/retry',args,exec)}));
  ctx.tools.register(defineTool({name:'benchforge_scene_status',description:"Read a concise current stage, real failures, quality outcomes and evidence/receipt paths. run_id excludes .sceneN. Use detail=true when the full saved SceneProgram or task reports are needed; ordinary progress checks use the default summary. Lifecycle completion and scene quality are separate fields.",parameters:{run_id:{type:'string',required:true},detail:{type:'boolean'}},output:observationOutput,/** 获取采集证据，按当前模型的图片输入能力保存并返回可见附件。 */ execute:(args,exec)=>observation(args.detail?'/inspect':'/observe',args,exec.signal)}));
  ctx.tools.register(defineTool({name:'benchforge_wait',description:"Wait for a scene, layout or asset run. Scene captures are tracked automatically. Use background=true to register or recover tracking of a run in this conversation. The host resumes this session when the run finishes or tracking fails. Continue independent work or finish this turn while waiting. Synchronous waits are bounded at 1..60 seconds. Read benchforge_scene_status with detail=true for full saved reports.",parameters:{run_id:{type:'string',required:true},background:{type:'boolean',description:'Register or recover one background observer for this session/run; no repeated wait calls are needed.'},timeout_seconds:{type:'integer',description:'Synchronous wait only, 1..60 seconds.'}},output:observationOutput,/** 通过统一服务入口执行 /observe 操作，并传递当前调用的取消信号。 */ execute:async(args,exec)=>{
    const {background,...request}=args;
    if(background){
      const observedAfter = Date.now();
      const scoped = await observation('/observe',request,exec.signal);
      exec.signal.throwIfAborted();
      return tracking.wait(exec.agent,request.run_id,scoped,{observedAfter});
    }
    return observation('/wait',request,exec.signal);
  }}));
  ctx.tools.register(defineTool({name:'benchforge_scene_blocker',description:'Record a concrete external blocker or two consecutive captures with the same unresolved failure. Requires a settled task owned by this session, saved service evidence, the specific reason and the missing condition. This never approves failed scenes. Ordinary quality defects must first be repaired; a pending capture already has automatic tracking.',
    parameters:{run_id:{type:'string',required:true},kind:{type:'string',enum:['external','no_progress'],required:true},
      reason:{type:'string',required:true},missing_condition:{type:'string',required:true},evidence:{type:'array',items:{type:'string'},required:true}},
    output,/** 登记采集任务的人工阻塞原因，暂停后台自动续接。 */ execute:(args,exec)=>tracking.block(exec.agent,args)}));
  ctx.tools.register(defineTool({name:'benchforge_task_code',description:"Run Python in this conversation's tasks/<workspace_id>/work directory with selected numeric and image libraries. workspace_id is a task label, not another conversation ID. Conversation files are readable at the same absolute paths as standard file tools; relative Python paths start in this task's work directory. Only this task directory is writable. Shared inputs must first be copied through authorized evidence/catalog tools. Service source, other conversations, credentials and network are unavailable. Submit returned source_path through production tools. Timeout 1..300 seconds, default 60; code up to 1 MiB.",parameters:{workspace_id:{type:'string',required:true},code:{type:'string',required:true},timeout_seconds:{type:'integer'}},output,/** 通过统一服务入口执行 /task-code 操作，并传递当前调用的取消信号。 */ execute:(args,exec)=>api('/task-code',args,exec.signal)}));
  ctx.tools.register(defineTool({name:'benchforge_refine',description:"Revise a captured scene from feedback or a complete SceneProgram. Can change layout, objects, materials, lights, cameras and actions. A capture-only parent stays capture-only unless target_items is explicitly supplied; otherwise the parent data target is inherited. source_revision optionally selects an earlier capture; preserves the source and train/dev family.",parameters:{request_key:requestKey,parent_task_id:{type:'string',required:true},source_revision:{type:'integer',description:'Optional captured revision to refine; use evidence to choose an earlier capture when the latest planning attempt failed.'},name:{type:'string',required:true},description:{type:'string',required:true},target_items:{type:'integer',description:'Number of dataset questions to export; this is not the scene entity count.'},layout:layoutInput,scene_program_path:programInput},output:taskRunOutput,/** 通过统一服务入口执行 /refine 操作，并传递当前调用的取消信号。 */ execute:(args,exec)=>submit('/refine',args,exec)}));
  ctx.tools.register(defineTool({name:'benchforge_cancel',description:"Cancel this run, preserving its artifacts and shared services.",parameters:{run_id:{type:'string',required:true}},output:cancellationOutput,/** 返回实际取消请求对应的受管作业。 */ execute:async(args,exec)=>{
    const run_id = captureRunId(args.run_id);
    return {...await api('/cancel',{run_id},exec.signal),run_id};
  }}));
}
