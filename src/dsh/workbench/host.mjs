import {createSceneQueries} from '../tools/scene/queries.mjs';
import fs from 'node:fs/promises';
import {createReadStream} from 'node:fs';
import {createInterface} from 'node:readline';
import {workspaceOperations} from './operations.mjs';
import path from 'node:path';
import {executePython, createPythonProcessScope} from '../host/python_process.mjs';
import z from '@deepseek-ai/schemastery';
import {sessionFamilyReader} from '../host/session_family.mjs';
import {createPreparationProducts} from './projections/preparation_products.mjs';
import {ApiError, assertSessionDirectory, exists, inside, matchesSession, readJson, scopeOf,
  scopeParams, token, workspaceRoots} from '../host/session_scope.mjs';
import {createDatasetReader, createPublicArtifactProjector, PUBLIC_FIELDS, QUALITY_FIELDS} from './projections/public_artifacts.mjs';
import {createWorkbenchReadRoutes} from './projections/routes.mjs';
import {createProductionProgress} from './projections/progress.mjs';
import {createRunReader, operationRunEntries, pipelineSceneIds} from './projections/run_queries.mjs';
import {WorkbenchRemote} from '../remote/lib/types/index.js';
import {TYPERT} from '../remote/lib/typert.host.js';
import {readAssessmentTemplate, readTaskTemplates} from './resources.mjs';
import {artifactProjection} from './artifacts/projection.mjs';
import {createArtifactEvidenceReader, resolveArtifactRoots} from './artifacts/scope.mjs';
import {conversationProducts, openConversationArtifact} from './artifacts/files.mjs';
import {launchEnvironmentOf} from '@deepseek-ai/dsh-launch-environment';
import {databaseEnvironment} from '../host/database_environment.mjs';
import {createExampleWorkbench} from '../examples/workbench.mjs';
import {createWorkspaceMigration} from '../access/migration.mjs';
import {createUserMessage} from '@deepseek-ai/dsh-llm';

export const name = 'benchforge-ui';
export const inject = ['connection', 'typert', 'workspaceRegistry', 'benchforgeCapture', 'benchforgeSessions',
  'sessionProjections', 'sessionQuery', 'benchforgeAccess', 'sessionController', 'agents'];
export const Config = z.object({
  workspacePaths: z.array(z.string()).default([]),
  maxRuns: z.natural().min(1).max(200).default(60),
  maxArtifacts: z.natural().min(1).max(1000).default(300),
  uiRevision: z.string().default('live-production-v1'),
  productionConfig: z.string().default(''),
  productionPython: z.string().default('python3'),
});

const STATUSES = ['draft', 'candidate', 'verified', 'rejected', 'superseded'];
/** 按字段白名单复制记录，构建面向界面的公开结果。 */
const pick = (row, fields) => Object.fromEntries(fields.filter((key) => key in row).map((key) => [key, row[key]]));
/** 为所有已知操作状态创建零计数汇总。 */
const counts = () => Object.fromEntries(STATUSES.map((key) => [key, 0]));
/** 通过可替换的受管 Python 执行器运行工作台辅助查询。 */
const execute = (config, args, options) =>
  (config._executePython || executePython)(config.productionPython, args, options);
/** 组合工作台查询参数，并按需绑定显式工作区路径。 */
function workbenchUrl(params, workspacePath) {
  const query = new URLSearchParams(params);
  if (workspacePath) query.set('workspace_path', workspacePath);
  return 'api/benchforge/workbench?' + query;
}

const taskTemplates = await readTaskTemplates();
const {publicPlan, publicReport} = createPublicArtifactProjector({pick, taskTemplates});
const {readDataset, readMedia, mediaReferences} = createDatasetReader({exists, readJson, pick, publicFields: PUBLIC_FIELDS});
const {sceneLibrary, sceneArtifacts, sceneMedia, scheduleSceneMovies, associatedCaptures} =
  createSceneQueries({workbenchUrl, pipelineSceneIds});
const productionProgress = createProductionProgress({scheduleSceneMovies, workbenchUrl});
const readRun = createRunReader({manifest, tailJsonl, publicPlan, runProducts});
const dispatchWorkbenchRead = createWorkbenchReadRoutes({/** 扫描当前授权范围内的流水线与公开数据集，汇总可见记录和最近计划。 */ discover: (...args) => discover(...args),
  /** 聚合当前会话的原生操作、流水线和采集进度，保留来源与快照时间。 */
  productionProgress: (...args) => productionProgress(...args), /** 解析运行标识并校验会话归属，读取流水线状态、产物清单、修复记录和计划。 */ readRun: (...args) => readRun(...args),
  /** 转发场景阶段产物查询，沿用统一会话范围校验。 */
  sceneArtifacts: (...args) => sceneArtifacts(...args), /** 转发场景媒体读取，保留原始请求的取消与范围信息。 */ sceneMedia: (...args) => sceneMedia(...args), readDataset, readMedia});

/** 核验数据集归属后，为前几个有效样本关联交付场景预览。 */
async function deliveryScenePreviews(root, dataset, previews, scope, itemCount = Number.MAX_SAFE_INTEGER) {
  if (typeof dataset !== 'string' || !Array.isArray(previews)) return [];
  if (!Number.isSafeInteger(itemCount) || itemCount < 1) return [];
  const directory = await inside(root, dataset);
  await assertSessionDirectory(root, directory, scope);
  const targets = previews.slice(0, 3).filter((row) => Number.isSafeInteger(row.row_index) &&
    row.row_index >= 0 && row.row_index < itemCount && Number.isSafeInteger(row.media_index) && row.media_index >= 0);
  if (!targets.length) return [];
  const wanted = new Set(targets.map((row) => row.row_index)), rows = new Map();
  const stream = createReadStream(path.join(directory, 'questions.jsonl'), {encoding: 'utf8'});
  const lines = createInterface({input: stream, crlfDelay: Infinity});
  let index = 0;
  try {
    for await (const line of lines) {
      if (!line.trim()) continue;
      if (wanted.has(index)) rows.set(index, JSON.parse(line));
      if (++index > Math.max(...wanted)) break;
    }
  } finally {lines.close(); stream.destroy();}
  const datasetId = token(root, path.relative(root.path, directory));
  return targets.flatMap((row) => {
    const item = rows.get(row.row_index);
    const url = item && mediaReferences(item, datasetId, row.row_index, root.path, scope)[row.media_index];
    return url ? [{title: String(row.title || '场景'), url: '/' + url}] : [];
  });
}

/** 将流水线计划、配置和步骤结果整理为按阶段展示的公开产物。 */
async function runProducts(id, root, directory, state, plan, scope) {
  const items = [], saved = publicPlan(plan);
  /** 为运行中的指定产品生成包含阶段与会话范围的访问链接。 */
  const link = (product, step = '') => workbenchUrl({view: 'run_product', run: id, product, step, ...scopeParams(scope)}, root.path);
  if (saved) {
    items.push({stage: 'S1', title: '评测计划', kind: 'json', data: saved, url: link('plan')});
    items.push({stage: 'S4', title: '评测配置', kind: 'json',
      data: {pipeline_id: saved.pipeline_id, inputs: saved.inputs, selections: saved.selections,
        template_contracts: saved.template_contracts}, url: link('config')});
  }
  for (const [stepId, step] of Object.entries(state.steps || {})) {
    const result = step.result || {};
    const receipt = result.delivery_receipt || result.summary;
    const hasDeliveryReceipt = receipt && ['quantity_contract', 'target_items', 'target_reached', 'delivery_satisfied', 'delivery_status']
      .some((key) => key in receipt);
    const candidates = [
      ...(hasDeliveryReceipt ? [{report: receipt, file: 'release/complete.json'}] : []),
      {file: path.join(directory, 'release', 'collection_report.json')},
      {file: path.join(directory, 'release', 'complete.json')},
      ...[result.report, result.quality_report,
        typeof result.dataset === 'string' ? path.join(result.dataset, 'quality_report.json') : null]
        .map((file) => ({file})),
    ];
    for (const candidate of candidates) {
      if (!candidate.report && (typeof candidate.file !== 'string' || !candidate.file.endsWith('.json'))) continue;
      try {
        const file = candidate.report ? '' : await inside(root, path.resolve(directory, candidate.file));
        const report = candidate.report || await readJson(file);
        if (!report || typeof report !== 'object') continue;
        const delivery = candidate.file?.includes('release') || path.basename(file) === 'delivery_report.json';
        const data = publicReport(report);
        if (delivery && report.scene_previews) data.scene_previews = await deliveryScenePreviews(root, result.dataset, report.scene_previews, scope, report.accepted ?? report.total);
        items.push({stage: 'S5', title: delivery ? '数据生产与质检报告' : path.basename(file) === 'quality_report.json' ? '数据质量报告' : '评测报告',
          ...(delivery ? {file: 'release/collection_report.json'} : {}),
          kind: 'json', step_id: stepId, artifact_id: step.artifact?.artifact_id,
          data, url: link('report', stepId)});
        break;
      } catch (error) { if (!['ENOENT'].includes(error.code) && !(error instanceof ApiError)) throw error; }
    }
  }
  return items;
}

/** 校验正整数分页参数，空值使用默认值并限制最大范围。 */
function integer(value, fallback, maximum) {
  if (value === null) return fallback;
  const number = Number(value);
  if (!Number.isSafeInteger(number) || number < 1 || number > maximum) throw new ApiError('Invalid pagination');
  return number;
}

/** 按关键词和能力筛选任务模板，计算分页及能力统计。 */
async function templateCatalog(url, catalog) {
  const query = (url.searchParams.get('q') || '').trim().toLocaleLowerCase();
  const capability = url.searchParams.get('capability') || '';
  const page = integer(url.searchParams.get('page'), 1, 1000000);
  const size = integer(url.searchParams.get('page_size'), 12, 48);
  const templates = catalog.tasks.flatMap((task) => task.templates.map((template) => ({
    ...pick(template, ['id', 'task_id', 'title', 'prompt_template', 'measurement_variant', 'output_kind',
      'effective_measurement_conditions', 'status']),
    task_name: task.name, family: task.family,
    capabilities: task.catalog_capability_links.map((node) => node.id),
  })));
  const filtered = templates.filter((row) => (!capability || row.capabilities.includes(capability)) &&
    (!query || [row.id, row.title, row.task_name, row.family, ...row.capabilities].join(' ').toLocaleLowerCase().includes(query)));
  return {source: catalog.meta.version, task_count: catalog.tasks.length, template_count: templates.length,
    capabilities: [...new Set(templates.flatMap((row) => row.capabilities))].sort(),
    total: filtered.length, page, pages: Math.max(1, Math.ceil(filtered.length / size)),
    items: filtered.slice((page - 1) * size, page * size), execution_status: 'catalog_design; check adapters before production'};
}

/** 逐行读取 JSONL 记录并提供连续行索引，在退出时关闭流和行读取器。 */
async function* jsonLines(file, signal) {
  const stream = createReadStream(file, {encoding: 'utf8', signal});
  const lines = createInterface({input: stream, crlfDelay: Infinity});
  let index = 0;
  try {
    for await (const line of lines) {
      signal?.throwIfAborted();
      if (line.trim()) yield {row: JSON.parse(line), index: index++};
    }
  } finally { lines.close(); stream.destroy(); }
}
/** 流式统计产物清单状态，并按界面预算截取可返回条目。 */
async function manifest(directory, limit, signal) {
  const file = path.join(directory, 'artifacts.jsonl');
  if (!await exists(file)) return {artifacts: [], counts: null, total: null, truncated: false};
  const byStatus = counts(), artifacts = [];
  let total = 0;
  for await (const {row} of jsonLines(file, signal)) {
    total++;
    if (row.status in byStatus) byStatus[row.status]++;
    if (limit) { artifacts.push(row); if (artifacts.length > limit) artifacts.shift(); }
  }
  return {artifacts, counts: byStatus, total, truncated: total > limit};
}
/** 流式保留 JSONL 文件最后若干行，同时统计总记录数。 */
async function tailJsonl(file, limit, signal) {
  if (!await exists(file)) return {rows: [], total: 0};
  const rows = [];
  let total = 0;
  for await (const {row} of jsonLines(file, signal)) {
    total++; rows.push(row); if (rows.length > limit) rows.shift();
  }
  return {rows, total};
}

/** 扫描当前授权范围内的流水线与公开数据集，汇总可见记录和最近计划。 */
async function discover(roots, config, signal, scope) {
  const runs = new Map(), datasets = new Map(), sources = [];
  let latestPlan = null, planSource = null;
  let unassociatedRecords = 0;
  /** 在源策略和会话归属校验后登记公开数据集，兼容标准和打包后的公开目录。 */
  const addDataset = async (root, directory, producer, runId) => {
    if (!directory) return;
    const policy=await readJson(path.join(root.path,'scene-library','policy.json'));
    if(policy?.enabled && (policy.excluded_paths || []).some(folder=>directory===folder || directory.startsWith(folder+path.sep)))return;
    const resolved = await inside(root, directory);
    let questionFile = path.join(resolved, 'questions.jsonl');
    if (!await exists(questionFile)) questionFile = path.join(resolved, 'public', 'items.jsonl');
    if (!await exists(questionFile)) return;
    const id = token(root, path.relative(root.path, resolved));
    const report = await readJson(path.join(resolved, 'quality_report.json'));
    datasets.set(id, {id, workspace: root.title, workspace_id: root.id, name: path.basename(resolved),
      version: path.relative(root.path, resolved).split(path.sep).join('/'), producer, run_id: runId || null,
      report: report ? pick(report, QUALITY_FIELDS) : null, public_format: path.basename(questionFile),
      state: 'unassessed', updated_at: (await fs.stat(questionFile)).mtime.toISOString()});
  };
  for (const root of roots) {
    signal?.throwIfAborted();
    let operations;
    try {
      operations = await workspaceOperations(config, root, scope, {limit: config.maxRuns + 1, signal,
        tools: ['pipeline_plan','plan','pipeline_run','pipeline_status','pipeline_artifacts','pipeline_repair_plan','build']});
    } catch (error) {
      sources.push({workspace: root.title, status: 'database_error', error: error.message}); continue;
    }
    sources.push({workspace: root.title, status: 'indexed', backend: 'postgresql', limit: config.maxRuns, truncated: operations.length > config.maxRuns});
    const entries = await operationRunEntries(config, root, scope, operations.slice(0, config.maxRuns), signal);
    for (const {row: operation, directory: artifact, result, snapshot, folder: directory, id, error: entryError} of entries) {
      try {
        if (entryError) throw entryError;
        if (!matchesSession({session_id: operation.session_id}, scope)) { if (!operation.session_id) unassociatedRecords++; continue; }
        if (['pipeline_plan', 'plan'].includes(operation.tool)) {
          if (operation.state === 'completed' && !latestPlan?.assessment) {
            const declared = operation.tool === 'pipeline_plan' ? result.plan : result.brief;
            const file = typeof declared === 'string' ? await inside(root, declared) : null;
            const saved = file ? await readJson(file) : null;
            if (saved) {
              const candidate = operation.tool === 'pipeline_plan' ? publicPlan(saved) :
                {objective: saved.objective || '', inputs: pick(saved, ['target_items']),
                  sources: saved.sources || [], notes: saved.notes || '', source: 'saved_benchmark_brief'};
              if (!latestPlan || candidate.assessment && !latestPlan.assessment || !latestPlan.assessment && !latestPlan.selections?.some((row) => row.template_ids?.length) &&
                  candidate.selections?.some((row) => row.template_ids?.length)) {
                latestPlan = candidate;
                planSource = {operation_id: operation.id, tool: operation.tool,
                  plan_path: file,
                  updated_at: (await fs.stat(file)).mtime.toISOString()};
              }
            }
          }
          continue;
        }
        if (operation.tool === 'build') {
          await addDataset(root, artifact, operation.id, null); continue;
        }
        if (!snapshot || runs.has(id)) continue;
        const {state} = snapshot;
        const record = await manifest(directory, 0, signal);
        runs.set(id, {id, label: path.relative(root.path, directory).split(path.sep).join('/'), workspace: root.title,
          workspace_id: root.id, operation_id: operation.id, state: state.state,
          steps: Object.keys(state.steps || {}).length, artifact_counts: record.counts,
          artifact_total: record.total, updated_at: snapshot.updated_at, authority: snapshot.authority});
        for (const [stepId, step] of Object.entries(state.steps || {})) {
          await addDataset(root, step.result?.dataset, stepId, id);
          // Nested runs are explicit persisted references, not filesystem scans.
          if (step.result?.steps) for (const [childId, child] of Object.entries(step.result.steps)) {
            await addDataset(root, child.result?.dataset, stepId + '/' + childId, id);
          }
        }
      } catch (error) {
        if (error.name === 'AbortError') throw error;
        sources.push({workspace: root.title, operation_id: operation.id, status: 'record_error', error: error.message});
      }
    }
  }
  const byStatus = counts();
  let manifests = 0;
  for (const run of runs.values()) if (run.artifact_counts) {
    manifests++; for (const key of STATUSES) byStatus[key] += run.artifact_counts[key];
  }
  return {runs: [...runs.values()].sort((a, b) => b.updated_at.localeCompare(a.updated_at)),
    datasets: [...datasets.values()].sort((a, b) => b.updated_at.localeCompare(a.updated_at)),
    plan: latestPlan, plan_source: planSource,
    counts: manifests ? byStatus : null, manifest_runs: manifests, sources,
    scope: scope.history ? 'explicit_workspace_history' : 'current_conversation_only',
    association: scope.session_id ? 'exact_session_context' : 'no_session', unassociated_records: unassociatedRecords,
    snapshot_at: new Date().toISOString()};
}

/** 创建统一工作台路由分发器，绑定会话范围、资源缓存及受限生产写入接口。 */
function createWorkbenchDispatcher(config, registered, family, synthesisFactory, execution, associations, readEvidence) {
  const exampleWorkbench = createExampleWorkbench({publicPlan});
  const limits = {maxRuns: 60, maxArtifacts: 300, productionPython:process.env.PYTHON || (process.platform==='win32' ? 'python' : 'python3'), ...config};
  limits._previewJobs = new Map(); limits._previewQueue = Promise.resolve(); limits._previewMedia = new Map();
  limits._libraryCache = new Map(); limits._libraryQueue = new Map();
  limits._preparation = createPreparationProducts({/** 解析计划真实文件，并确认其目录属于指定父会话。 */ validatePlan: async (root, planPath, parentId) => {
    const file = await inside(root, planPath);
    await assertSessionDirectory(root, path.dirname(file), {session_id: parentId, session_ids: [], history: false});
    return file;
  }});
  return /** 按视图分发工作台请求，解析原生会话族与授权根目录后执行相应读写路径。 */ async (url, request) => {
    const view = url.searchParams.get('view') || 'summary';
    if (view === 'runtime') return {ui_revision: limits.uiRevision || '',
      preset_id: 'benchforge', preset_name: 'BenchForge'};
    const parentScope = scopeOf(url);
    const related = !view.startsWith('execution_') && !parentScope.history && /^session-[a-zA-Z0-9-]+$/.test(parentScope.session_id)
      ? await family(parentScope.session_id, request.signal) : [];
    const evidence = readEvidence && parentScope.session_id && !parentScope.history
      ? await readEvidence(parentScope.session_id, related, request.signal) : [];
    const roots = evidence.length
      ? await resolveArtifactRoots(limits, registered(), url.searchParams.get('workspace_path') || '', evidence)
      : await workspaceRoots(limits, registered(), url.searchParams.get('workspace_path') || '');
    const sessions = related.map((row) => row.id);
    const scope = scopeOf(url, sessions), scoped = {...limits, _sessionIds: sessions, _family: related};
    const example = await exampleWorkbench({roots, scope, url, request});
    if (example) return example.value;
    if (view === 'products') {
      if (request.method !== 'GET') throw new ApiError('Conversation products require GET', 405);
      if (!scope.session_id || scope.history || !evidence.length) throw new ApiError('Conversation products are unavailable', 403);
      return conversationProducts({sessionId: scope.session_id, evidence, roots, maxArtifacts: limits.maxArtifacts, signal: request.signal});
    }
    if (view === 'conversation_artifact') {
      if (request.method !== 'GET') throw new ApiError('Conversation artifacts require GET', 405);
      if (!scope.session_id || scope.history || !evidence.length) throw new ApiError('Conversation artifacts are unavailable', 403);
      return openConversationArtifact(url.searchParams.get('artifact') || '', {evidence, roots, request});
    }
    if (view === 'progress' || view === 'stage_artifacts' || view === 'stage_media') {
      const saved = evidence.flatMap((owner) => owner.captureIds || []);
      scoped._captureIds = [...new Set([...await associatedCaptures(roots, scope, associations), ...saved])];
    }
    let value;
    if (view === 'execution_status' || view === 'execution_control') {
      if(roots.length !== 1 || !scope.session_id || scope.history || !execution)
        throw new ApiError('Execution adjustment requires the current workspace and production parent',400);
      if(request.headers.get('sec-fetch-site') === 'cross-site')throw new ApiError('Cross-site execution control is not allowed',403);
      if(view === 'execution_status') {
        if(request.method !== 'GET')throw new ApiError('Execution status requires GET',405);
        value=await execution.observe(roots[0].path,scope.session_id);
      } else {
        if(request.method !== 'POST')throw new ApiError('Execution adjustment requires POST',405);
        const body=await request.text();
        if(body.length>2048)throw new ApiError('Execution adjustment is too large',413);
        let tuning;
        try {tuning=JSON.parse(body);}catch {throw new ApiError('Invalid execution adjustment JSON');}
        if(!tuning || typeof tuning!=='object' || Array.isArray(tuning) || Object.keys(tuning).some(key=>!['reasoning_effort','compact_history'].includes(key)) ||
          tuning.compact_history!==undefined && typeof tuning.compact_history!=='boolean')throw new ApiError('Invalid execution adjustment');
        value=await execution.arm(roots[0].path,scope.session_id,tuning,request.signal);
      }
    }
    else if (view === 'synthesis_start' || view === 'synthesis_status') {
      if (roots.length !== 1 || !scope.session_id || scope.history)
        throw new ApiError('Preparation requires the current workspace and parent conversation',400);
      if (request.headers.get('sec-fetch-site') === 'cross-site')
        throw new ApiError('Cross-site preparation is not allowed',403);
      if (!synthesisFactory) throw new ApiError('Native preparation service is unavailable',503);
      const controller=synthesisFactory(roots[0].path);
      if (view === 'synthesis_status') {
        if (request.method !== 'GET') throw new ApiError('Preparation status requires GET',405);
        value=await controller.read({parentId:scope.session_id,batchKey:url.searchParams.get('batch_key')});
      } else {
        if (request.method !== 'POST') throw new ApiError('Preparation startup requires POST',405);
        const body=await request.text();
        if (body.length > 512000) throw new ApiError('Preparation request is too large',413);
        let preparation;
        try {preparation=JSON.parse(body);} catch {throw new ApiError('Invalid preparation JSON');}
        if (!preparation || typeof preparation.plan_path !== 'string')
          throw new ApiError('Preparation needs the saved parent plan');
        const file=await inside(roots[0],preparation.plan_path);
        await assertSessionDirectory(roots[0],path.dirname(file),parentScope);
        value=await controller.start({parentId:scope.session_id,batchKey:preparation.batch_key,
          planPath:file,tasks:preparation.tasks},request.signal);
      }
      limits._preparation.remember(roots[0],scope.session_id,value);
    }
    else if (view === 'plan_edit') {
      if (request.method !== 'POST') throw new ApiError('Plan editing requires POST',405);
      if (roots.length !== 1 || !scope.session_id) throw new ApiError('A current workspace and conversation are required',400);
      if (request.headers.get('sec-fetch-site') === 'cross-site') throw new ApiError('Cross-site plan editing is not allowed',403);
      const body = await request.text();
      if (body.length > 512000) throw new ApiError('Plan edit is too large',413);
      let edit;
      try {edit=JSON.parse(body);} catch {throw new ApiError('Invalid plan edit JSON');}
      if (typeof edit?.plan_path !== 'string' ||
          !(typeof edit.markdown === 'string' && edit.markdown.trim() || edit.assessment && typeof edit.assessment === 'object'))
        throw new ApiError('A saved plan and complete edited Markdown document are required');
      const file=await inside(roots[0],edit.plan_path);
      await assertSessionDirectory(roots[0],path.dirname(file),parentScope);
      const helper='server.plan_edit_cli';
      const args=['-B','-m',helper,'--workspace',roots[0].path,'--session',scope.session_id,'--plan',file];
      if (config.productionConfig) args.push('--config',config.productionConfig);
      const process=execute(limits,args,
        {signal:request.signal,timeout:20000,maxBuffer:256000});
      process.child.stdin.on('error',()=>{});
      process.child.stdin.end(JSON.stringify({assessment:edit.assessment,markdown:edit.markdown,
        base_markdown:edit.base_markdown,split:edit.split}));
      value=JSON.parse((await process).stdout);
      if (value.error) throw new ApiError(value.error,value.status_code || 400);
    }
    else if (view === 'stage_review') {
      if (roots.length !== 1 || !scope.session_id) throw new ApiError('A current workspace and conversation are required',400);
      if (request.headers.get('sec-fetch-site') === 'cross-site') throw new ApiError('Cross-site stage review is not allowed',403);
      const body = request.method === 'POST' ? await request.text() : '{}';
      if (body.length > 16000) throw new ApiError('Stage instruction is too large',413);
      const helper = 'server.stage_review_cli';
      const args=['-B','-m',helper,'--workspace',roots[0].path,'--session',scope.session_id,'--request',body];
      if (config.productionConfig) args.push('--config',config.productionConfig);
      const result = await execute(limits,args,
        {signal:request.signal,timeout:12000,maxBuffer:128000});
      value = JSON.parse(result.stdout);
    }
    else if (request.method === 'POST') {
      if (view !== 'scene_selection') throw new ApiError('This workbench operation is read-only', 405);
      // DSH authorizes the connection before dispatch and constructs a virtual Request.
      // Its virtual origin is not the browser's origin; reject an explicit cross-site fetch instead.
      if (request.headers.get('sec-fetch-site') === 'cross-site') throw new ApiError('Cross-site scene selection is not allowed', 403);
      const body = await request.text();
      if (body.length > 128000) throw new ApiError('Scene selection is too large', 413);
      let selection;
      try {selection = JSON.parse(body);} catch {throw new ApiError('Invalid scene selection JSON');}
      if (!selection || typeof selection !== 'object' || Array.isArray(selection)) throw new ApiError('Invalid scene selection');
      value = await sceneLibrary(limits, roots, url, request.signal, selection);
    }
    else if (view === 'scene_library' || view === 'scene_selection') value = await sceneLibrary(limits, roots, url, request.signal);
    else if (view === 'catalog') value = await templateCatalog(url, taskTemplates);
    else if (view === 'plan_template') value = await readAssessmentTemplate();
    else if (view === 'run_product' && url.searchParams.get('product') === 'preparation') {
      if (roots.length !== 1 || !scope.session_id || scope.history)
        throw new ApiError('Preparation previews require the current workspace and parent conversation',400);
      return await limits._preparation.open(roots[0], scope.session_id, related, {
        batchKey: url.searchParams.get('batch_key'), childId: url.searchParams.get('child_id'), file: url.searchParams.get('file'),
      }, request.signal);
    }
    else if (['summary', 'progress', 'run', 'run_product', 'stage_artifacts', 'stage_media', 'dataset', 'media'].includes(view)) {
      const response = await dispatchWorkbenchRead({view, request, url, roots, limits, scoped, scope, sessions});
      if (response instanceof Response) return response;
      value = response;
    } else throw new ApiError('Unsupported workbench view');
    return value;
  };
}

/** 将工作台分发结果转为禁止缓存的响应，并规范业务错误状态。 */
function fetchWorkbench(dispatch) {
  return /** 把普通工作台结果包装为禁止缓存的响应，将已知业务异常转换为适当 HTTP 状态。 */ async (request) => {
    try {
      const value = await dispatch(new URL(request.url), request);
      return value instanceof Response ? value : Response.json(value, {headers: {'cache-control': 'no-store'}});
    } catch (error) {
      if (error.name === 'AbortError') throw error;
      return Response.json({error: error.message}, {status: error.status || (error.code === 'ENOENT' ? 404 : 500), headers: {'cache-control': 'no-store'}});
    }
  };
}

/** 创建供 HTTP 接入使用的工作台处理器，注入已注册工作区及会话族查询。 */
export function createWorkbenchHandler(config, registered = () => [], family = async () => [], synthesisFactory, execution, associations) {
  return fetchWorkbench(createWorkbenchDispatcher(config, registered, family, synthesisFactory, execution, associations));
}

/** 装配宿主工作台、原生远程入口、资源投影和受管 Python 生命周期。 */
export async function apply(ctx, config) {
  const environment = await databaseEnvironment(config.productionConfig, launchEnvironmentOf(ctx));
  const processes = createPythonProcessScope();
  ctx.effect(() => () => processes.dispose(), 'benchforge-workbench: Python process lifecycle');
  config = {...config, productionConfig: config.productionConfig ? path.resolve(config.productionConfig) : '',
    /** 把数据库环境与请求环境合并后，通过当前宿主持有的进程作用域执行 Python。 */
    _executePython: (python, args, options = {}) => processes.execute(python, args,
      {...options, env: {...environment, ...options.env}})};
  const {execution, preparation} = ctx.benchforgeSessions;
  const {associations} = ctx.benchforgeCapture;
  const readEvidence = createArtifactEvidenceReader(ctx.get('sessionQuery'));
  await ctx.sessionProjections.register(artifactProjection);
  const baseDispatch = createWorkbenchDispatcher(config, () => ctx.workspaceRegistry.list(), sessionFamilyReader(ctx),
    preparation, execution, associations, readEvidence);
  /** 执行统一工作台路由，并在阶段复核写入后立即协调生产续接状态。 */
  const dispatch = async (url, request) => {
    const result = await baseDispatch(url, request);
    if(request.method==='POST'&&url.searchParams.get('view')==='stage_review') {
      result.continuation_managed=await ctx.benchforgeSessions.production.refresh(url.searchParams.get('session_id'));
    }
    return result;
  };
  const migration = createWorkspaceMigration({
    /** 通过官方查询服务观察指定会话，保留调用方的读取选项。 */
    observe: (id, options) => ctx.sessionQuery.observeSession(id, options),
    /** 读取指定根目录与会话的有界操作记录，用于迁移和产物核验。 */
    readOperations: (root, id, signal) => workspaceOperations(config, {path: root}, {session_id: id}, {limit: 201, signal}),
    access: ctx.benchforgeAccess, sessions: ctx.sessionController, agents: ctx.agents, createMessage: createUserMessage,
  });
  // Migration is reachable only through the Remote's owner principal check, not
  // the file-fetch route or any model tool, and never accepts a filesystem manifest.
  ctx.provide('benchforgeWorkbench', new WorkbenchRemote(/** 在原生远程入口分流工作区迁移的预览与提交，其余请求交给统一工作台路由。 */ async (url, request) => {
    if (url.searchParams.get('view') !== 'workspace_migration') return dispatch(url, request);
    const id = url.searchParams.get('session_id');
    if (request.method === 'GET') return migration.preview(id, request.signal);
    const body = await request.text();
    if (body.length > 512000) throw new ApiError('Migration selection is too large', 413);
    let selection;
    try {selection = JSON.parse(body);} catch {throw new ApiError('Invalid migration selection JSON', 400);}
    return migration.migrate(id, selection, request.signal);
  }));
  // Local file overlays do not have a bare package row for the Typert loader.
  ctx.effect(() => ctx.typert.register(TYPERT), 'benchforge-workbench: Remote API');
  const dispose = ctx.connection.fetch.register({path: '/api/benchforge/workbench', methods: ['GET', 'POST'], requestBody: 'buffered',
    fetch: fetchWorkbench(dispatch)});
  ctx.effect(() => dispose, 'benchforge-ui: authenticated artifacts and scene selection API');
}
