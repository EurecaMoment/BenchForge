import path from 'node:path';
import {executePython} from '../../host/python_process.mjs';
import {ApiError, inside, matchesSession, readJson, resolveToken, token} from '../../host/session_scope.mjs';

const PIPELINE_TOOLS = new Set(['pipeline_run', 'pipeline_status', 'pipeline_artifacts', 'pipeline_repair_plan']);

/** 一次批量读取当前工作区及可信会话族的数据库快照，不以文件副本作为状态兜底。 */
export async function queryRunSnapshots(config, root, scope, runs, signal, view = 'summary') {
  const paths = [...new Set(runs)];
  if (!paths.length) return new Map();
  if (!['summary', 'detail'].includes(view) || view === 'detail' && paths.length !== 1)
    throw new Error('Pipeline detail requires one run; batches use summary');
  const sessions = [...new Set([scope.session_id, ...(scope.session_ids || [])].filter(Boolean))];
  const args = ['-B', '-m', 'server.workbench_runs', '--workspace', root.path,
    '--sessions', JSON.stringify(sessions), '--runs', JSON.stringify(paths), '--view', view];
  if (scope.history) args.push('--history');
  if (config.productionConfig) args.push('--config', config.productionConfig);
  const {stdout} = await (config._executePython || executePython)(config.productionPython, args,
    {signal, timeout: 12000, maxBuffer: (view === 'detail' ? 20 : 16) * 1024 * 1024});
  const result = JSON.parse(stdout);
  if (!Array.isArray(result.runs)) throw new Error('Invalid authoritative run response');
  const requested = new Set(paths.map(value => path.resolve(root.path, value)));
  for (const row of result.runs) {
    if (!requested.has(row.run) || row.authority !== 'postgresql' || !row.state ||
        !matchesSession({session_id: row.session_id}, scope))
      throw new Error('Authoritative run response does not match its query scope');
  }
  return new Map(result.runs.map(row => [row.run, row]));
}

/** 读取单个运行的权威状态；未登记或不属于当前会话的路径统一返回不存在。 */
export async function readRunSnapshot(config, root, scope, directory, signal) {
  const records = await queryRunSnapshots(config, root, scope, [directory], signal, 'detail');
  const record = records.get(directory);
  if (!record) throw new ApiError('No authoritative pipeline state is available for this conversation', 404);
  return record;
}

/** 从有界操作账本提取运行引用，再批量查询一次；文件只提供引用与工件内容。 */
export async function operationRunEntries(config, root, scope, rows, signal) {
  const entries = [];
  for (const row of rows) {
    signal?.throwIfAborted();
    const entry = {row};
    entries.push(entry);
    if (!matchesSession({session_id: row.session_id}, scope)) continue;
    try {
      entry.directory = await inside(root, row.artifact);
      entry.request = await readJson(path.join(entry.directory, 'request.json')) || {};
      entry.result = await readJson(path.join(entry.directory, 'result.json')) || {};
      if (!PIPELINE_TOOLS.has(row.tool)) continue;
      const requested = entry.request.arguments?.run || entry.result.run || path.join(entry.directory, 'pipeline_run');
      entry.folder = await inside(root, requested);
      entry.id = token(root, path.relative(root.path, entry.folder));
    } catch (error) {
      if (signal?.aborted || error.name === 'AbortError') throw error;
      entry.error = error;
    }
  }
  const candidates = entries.filter(entry => entry.folder && !entry.error);
  try {
    const records = await queryRunSnapshots(config, root, scope, candidates.map(entry => entry.folder), signal);
    for (const entry of candidates) entry.snapshot = records.get(entry.folder);
  } catch (error) {
    if (signal?.aborted || error.name === 'AbortError') throw error;
    for (const entry of candidates) entry.error = error;
  }
  return entries;
}

/** 解析运行标识并校验会话归属，读取流水线状态、产物清单、修复记录和计划。 */
export function createRunReader({manifest, tailJsonl, publicPlan, runProducts}) {
  /** 从数据库取得状态，再读取已授权运行目录中的展示工件。 */
  return async function readRun(id, roots, config, signal, scope) {
    const {root, directory} = await resolveToken(id, roots);
    const {state, plan, updated_at, authority} = await readRunSnapshot(config, root, scope, directory, signal);
    const [artifacts, repairs] = await Promise.all([
      manifest(directory, config.maxArtifacts, signal),
      tailJsonl(path.join(directory, 'repair-plans.jsonl'), config.maxArtifacts, signal),
    ]);
    return {id, label: path.relative(root.path, directory).split(path.sep).join('/'), workspace: root.title,
      authority, updated_at, state: Object.fromEntries(['state', 'run_id', 'next', 'missing_binding', 'step']
        .filter(key => key in state).map(key => [key, state[key]])),
      steps: Object.entries(state.steps || {}).map(([key, value]) => ({id: key, state: value.state,
        error: value.error, artifact_id: value.artifact?.artifact_id})),
      plan: publicPlan(plan), scene_ids: pipelineSceneIds(state), stage_artifacts: await runProducts(id, root, directory, state, plan, scope),
      plan_source: plan ? {plan_path: path.join(directory, 'plan.json'), run_id: id} : null,
      artifacts: artifacts.artifacts, artifact_total: artifacts.total, artifact_counts: artifacts.counts,
      artifacts_truncated: artifacts.truncated, repairs: repairs.rows, repair_total: repairs.total};
  };
}

/** 递归收集流水线结果中的合法场景运行标识，并限制返回数量。 */
export function pipelineSceneIds(state) {
  const ids = new Set();
  /** 从嵌套步骤结果中添加唯一场景标识，达到数量预算后停止继续收集。 */
  const collect = (result) => {
    if (!result || ids.size >= 12) return;
    if (typeof result.run_id === 'string' && /^sf_[a-zA-Z0-9_-]+$/.test(result.run_id)) ids.add(result.run_id);
    for (const step of Object.values(result.steps || {})) collect(step.result);
  };
  for (const step of Object.values(state?.steps || {})) collect(step.result);
  return [...ids];
}
