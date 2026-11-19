import path from 'node:path';
import {executePython} from '../../host/python_process.mjs';
import {matchesSession, readJson, scopeOf} from '../../host/session_scope.mjs';
import {stageOfStep} from '../../production/stages.mjs';
import {collectionSummary} from '../../production/collection.mjs';
import {workspaceOperations} from '../operations.mjs';
import {operationRunEntries} from './run_queries.mjs';

/** 用宿主持有的进程作用域运行只读辅助查询。 */
const execute = (config, args, options) =>
  (config._executePython || executePython)(config.productionPython, args, options);
const STAGE_OPERATIONS = [
  ['S1', /^(plan|pipeline_plan|brief|design|literature)/],
  ['S2', /^(capture|scene|asset|reference|(?:benchforge|spatialforge)\.(start|capture|asset|refine))/],
  ['S3', /^(acquire|collect|annotat|normaliz|structur|evidence|trajectory|(?:benchforge|spatialforge)\.(evidence|task_code))/],
  ['S4', /^(build|synthesi|templates\.produce|dataset|produce)/],
  ['S5', /^(score|screen|evaluat|verif|quality|report|package|(?:templates|data)\.package)/],
];
/** 根据标准步骤契约及兼容操作模式推断生产阶段。 */
const stageOf = (operation) => stageOfStep({uses: operation}) || STAGE_OPERATIONS.find(([, pattern]) => pattern.test(operation || ''))?.[0] || null;

/** 聚合当前会话的原生操作、流水线和采集进度，保留来源与快照时间。 */
export function createProductionProgress({scheduleSceneMovies, workbenchUrl}) {
  /** 查询真实运行快照后组合当前会话与子会话的阶段进度。 */
  return async function productionProgress(roots, config, url, signal) {
    const operations = [], pipelines = [], sources = [], remoteIds = new Set();
    for (const id of config._captureIds || []) remoteIds.add(id);
    let unassociatedRecords = 0;
    const scope = scopeOf(url, config._sessionIds);
    const seen = new Set();
    for (const root of roots) {
      let rows;
      try {
        rows = await workspaceOperations(config, root, scope, {limit: config.maxRuns, signal});
      } catch (error) {sources.push({workspace: root.title, status: 'database_error', error: error.message}); continue;}
      sources.push({workspace: root.title, status: 'indexed', backend: 'postgresql', limit: config.maxRuns});
      const entries = await operationRunEntries(config, root, scope, rows, signal);
      for (const {row, request, snapshot, folder, id, error: entryError} of entries) {
        signal?.throwIfAborted();
        try {
          if (entryError) throw entryError;
          if (!matchesSession({session_id: row.session_id}, scope)) { if (!row.session_id) unassociatedRecords++; continue; }
          operations.push({id: row.id, tool: row.tool, state: row.state, error: row.error, stage: stageOf(row.tool),
            workspace: root.title, session_id: row.session_id, started_at: request.time || null});
          if (!['pipeline_run', 'pipeline_status', 'pipeline_artifacts', 'pipeline_repair_plan'].includes(row.tool)) continue;
          if (!snapshot || seen.has(id)) continue;
          seen.add(id);
          const {state, plan} = snapshot;
          const planned = plan?.steps || [];
          const steps = planned.map((step) => {
            const actual = state.steps?.[step.id];
            if (actual?.result?.run_id?.startsWith('sf_')) remoteIds.add(actual.result.run_id);
            const captureWait = ['benchforge.observe', 'spatialforge.observe'].includes(step.uses) && step.await_terminal && planned.some((producer) =>
              ['benchforge.capture', 'spatialforge.capture'].includes(producer.uses) && step.arguments?.run_id === '$' + producer.id + '.run_id');
            return {id: step.id, uses: step.uses,
              stage: stageOfStep(step) || (captureWait ? 'S2' : stageOf(step.uses)),
              state: actual?.state || 'unknown',
              collection: collectionSummary(actual?.result),
              error: actual?.error || null};
          });
          const completed = steps.filter((step) => step.state === 'completed').length;
          // The runner persists results after a step; an unrecorded step is not a measured execution.
          const current = steps.find((step) => step.id === state.step) || steps.find((step) => step.state !== 'completed');
          pipelines.push({id, label: path.relative(root.path, folder).split(path.sep).join('/'),
            session_id: snapshot.session_id,
            state: state.state, completed, total: plan ? planned.length : null, current,
            next: state.next, missing_binding: state.missing_binding, steps,
            updated_at: snapshot.updated_at, authority: snapshot.authority});
        } catch (error) {
          if (error.name === 'AbortError') throw error;
          sources.push({workspace: root.title, operation_id: row.id, status: 'record_error', error: error.message});
        }
      }
    }
    let scenes = {status: 'not_configured', runs: []};
    const session = url.searchParams.get('session_id') || '';
    if (config.productionConfig && roots.length && /^session-[a-zA-Z0-9-]+$/.test(session)) {
      try {
        const helper = 'pipelines.benchmark.progress.production_observer';
        const {stdout} = await execute(config, ['-B', '-m',helper, '--config', config.productionConfig,
          '--session', session, '--sessions', JSON.stringify(config._sessionIds || []),
          '--workspace', roots[0].path,
          '--run-ids', JSON.stringify([...remoteIds].slice(0, 12))],
        {signal, timeout: 12000, maxBuffer: 2 * 1024 * 1024});
        scenes = JSON.parse(stdout);
        const removed=new Set();
        for(const root of roots) {
          const policy=await readJson(path.join(root.path,'scene-library','policy.json'));
          if(policy?.enabled)for(const id of policy.excluded_tasks || [])removed.add(id);
        }
        scenes.runs=(scenes.runs || []).map(row=>({...row,tasks:(row.tasks || []).filter(task=>!removed.has(task.id))})).filter(row=>row.tasks.length);
        scheduleSceneMovies(config, session, scenes.runs || [], [...remoteIds].slice(0, 12));
      } catch (error) {
        if (signal?.aborted) throw error;
        scenes = {status: 'unavailable', error: 'Production observer could not read the service (' + (error.code || error.name) + ')', runs: []};
      }
    }
    const preparation = new Map();
    for (const root of roots) {
      const drafts = await config._preparation.read(root, session, config._family || [], signal);
      for (const [childId, draft] of drafts) preparation.set(childId, {root, ...draft});
    }
    const parallel_tasks = (config._family || []).map((child, index) => {
      const owned = operations.filter((row) => row.session_id === child.id);
      const saved = pipelines.filter((row) => row.session_id === child.id);
      const draft = preparation.get(child.id);
      const public_artifacts = [...(draft?.files || []).map((file) => ({
        title: file.title, kind: 'preparation', accepted: false, revision: file.revision,
        url: workbenchUrl({view: 'run_product', product: 'preparation', batch_key: draft.batchKey,
          child_id: child.id, file: file.file, session_id: session}, draft.root.path),
      })), ...saved.filter((row) => row.completed > 0).map((row) => ({
        title: (child.label || '子任务') + '计划', url: workbenchUrl({view: 'run_product', product: 'plan', run: row.id, session_id: session}, roots[0]?.path),
        revision: row.steps.filter((step) => step.state === 'completed').map((step) => step.id).join(','),
      }))];
      return {id: child.id, display_name: child.label || '子任务 ' + (index + 1), state: child.state || 'unknown',
        stage: owned.find((row) => row.stage)?.stage || (/标注|annotation/i.test(child.label || '') ? 'S3' :
          /题目|question/i.test(child.label || '') ? 'S4' : /场景|scene/i.test(child.label || '') ? 'S2' : null),
        artifact_count: public_artifacts.length, public_artifacts,
        ...(draft ? {preparation: {status: 'preparing', accepted: false, artifact_count: draft.files.length,
          reasoningSelection: draft.reasoningSelection}} : {})};
    });
    const {current_collection = null, generation = null, ...observedScenes} = scenes;
    return {operations, pipelines, scenes: observedScenes, current_collection, generation, parallel_tasks, sources, snapshot_at: new Date().toISOString(),
      scope: scope.history ? 'explicit_workspace_history' : 'current_conversation_only',
      association: scope.session_id ? config._sessionIds?.length ? 'parent_and_verified_child_context' : 'exact_session_context' : 'no_session',
      unassociated_records: unassociatedRecords};
  };
}
