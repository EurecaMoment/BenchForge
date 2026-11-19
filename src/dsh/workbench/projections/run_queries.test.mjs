import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {createRunReader, operationRunEntries, queryRunSnapshots, readRunSnapshot} from './run_queries.mjs';
import {createProductionProgress} from './progress.mjs';

/** 创建带伪造文件副本的工作区，查询器应只使用返回的数据库状态。 */
async function fixture(t) {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'benchforge-run-query-'));
  t.after(() => fs.rm(directory, {recursive: true, force: true}));
  const root = {path: await fs.realpath(directory), title: 'test', id: 'root'};
  const run = path.join(root.path, 'operations/run/pipeline_run');
  await fs.mkdir(run, {recursive: true});
  await fs.writeFile(path.join(run, 'state.json'), '{"state":"completed"}');
  await fs.writeFile(path.join(run, 'plan.json'), '{"steps":[]}');
  const rows = ['run', 'observe'].map(id => ({id, artifact: `operations/${id}`, tool: 'pipeline_run',
    state: 'completed', session_id: 'session-a'}));
  for (const row of rows) {
    const folder = path.join(root.path, row.artifact);
    await fs.mkdir(folder, {recursive: true});
    await fs.writeFile(path.join(folder, 'request.json'), JSON.stringify({arguments: {run},
      context: {session_id: 'forged-session'}, time: '2026-11-19'}));
    await fs.writeFile(path.join(folder, 'result.json'), JSON.stringify({run}));
  }
  const snapshot = {run, session_id: 'session-a', authority: 'postgresql', updated_at: '2026-11-19T03:00:00Z',
    state: {state: 'running', steps: {collect: {state: 'running'}}},
    plan: {steps: [{id: 'collect', uses: 'collection.validate'}]}};
  return {root, run, rows, snapshot, scope: {session_id: 'session-a', session_ids: ['session-b']}};
}

test('operation references batch once and use database state despite changed projections', /** 验证批量查询去重并忽略被改动的工作区状态副本。 */ async t => {
  const f = await fixture(t), calls = [], limits = [];
  const config = {productionPython: 'python', productionConfig: '/service/config.json',
    /** 保存真实调用参数，返回与文件副本不同的权威快照。 */
    _executePython: async (_python, args, options) => {calls.push(args); limits.push(options.maxBuffer);
      return {stdout: JSON.stringify({runs: [f.snapshot]})};}};
  const entries = await operationRunEntries(config, f.root, f.scope, f.rows);
  assert.equal(calls.length, 1);
  assert.deepEqual(JSON.parse(calls[0][calls[0].indexOf('--runs') + 1]), [f.run]);
  assert.deepEqual(JSON.parse(calls[0][calls[0].indexOf('--sessions') + 1]), ['session-a', 'session-b']);
  assert.equal(calls[0][calls[0].indexOf('--config') + 1], '/service/config.json');
  assert.equal(calls[0][calls[0].indexOf('--view') + 1], 'summary');
  assert.equal(entries.length, 2);
  assert.ok(entries.every(entry => entry.snapshot.state.state === 'running'));
  await fs.unlink(path.join(f.run, 'state.json'));
  assert.equal((await readRunSnapshot(config, f.root, f.scope, f.run)).plan.steps.length, 1);
  assert.equal(calls[1][calls[1].indexOf('--view') + 1], 'detail');
  assert.deepEqual(limits, [16 * 1024 * 1024, 20 * 1024 * 1024]);
});

test('database errors or foreign snapshots never fall back to workspace state', /** 验证数据库故障和越权回包不会触发文件状态兜底。 */ async t => {
  const f = await fixture(t);
  const config = {productionPython: 'python', _executePython: async () => {throw new Error('database unavailable');}};
  const entries = await operationRunEntries(config, f.root, f.scope, f.rows);
  assert.ok(entries.every(entry => !entry.snapshot && entry.error.message === 'database unavailable'));
  await assert.rejects(readRunSnapshot(config, f.root, f.scope, f.run), /database unavailable/);
  config._executePython = async () => ({stdout: JSON.stringify({runs: [{...f.snapshot, session_id: 'foreign'}]})});
  await assert.rejects(queryRunSnapshots(config, f.root, f.scope, [f.run]), /query scope/);
  config._executePython = async () => ({stdout: '{"runs":[]}'});
  await assert.rejects(readRunSnapshot(config, f.root, f.scope, f.run), error => error.status === 404);
});

test('progress uses the same authoritative plan and state as run details', /** 验证进度阶段、更新时间和会话身份来自权威快照。 */ async t => {
  const f = await fixture(t), modules = [];
  const config = {maxRuns: 60, productionPython: 'python', _sessionIds: ['session-b'],
    _preparation: {read: async () => new Map()},
    /** 按查询模块提供操作索引或权威运行结果，不允许读取文件副本补状态。 */
    _executePython: async (_python, args) => {
      const module = args[args.indexOf('-m') + 1]; modules.push(module);
      return {stdout: JSON.stringify(module === 'server.workspace_operations' ? {operations: f.rows} : {runs: [f.snapshot]})};
    }};
  const progress = createProductionProgress({scheduleSceneMovies() {}, workbenchUrl: () => ''});
  const value = await progress([f.root], config, new URL('http://localhost/?session_id=session-a'));
  assert.deepEqual(modules, ['server.workspace_operations', 'server.workbench_runs']);
  assert.equal(value.pipelines.length, 1);
  assert.equal(value.pipelines[0].state, 'running');
  assert.equal(value.pipelines[0].total, 1);
  assert.equal(value.pipelines[0].updated_at, f.snapshot.updated_at);
  assert.equal(value.pipelines[0].authority, 'postgresql');
  assert.equal(value.operations[0].session_id, 'session-a');
});

test('run details and products consume one database snapshot', /** 验证详情与阶段产物使用同一次数据库读取而非各自打开状态副本。 */ async t => {
  const f = await fixture(t);
  let productState;
  const config = {maxArtifacts: 20, productionPython: 'python',
    _executePython: async () => ({stdout: JSON.stringify({runs: [f.snapshot]})})};
  const reader = createRunReader({
    manifest: async () => ({artifacts: [], total: 0, counts: {}, truncated: false}),
    tailJsonl: async () => ({rows: [], total: 0}), publicPlan: value => value,
    runProducts: async (_id, _root, _directory, state) => {productState = state; return [];},
  });
  const id = Buffer.from(JSON.stringify([f.root.id, path.relative(f.root.path, f.run)])).toString('base64url');
  const value = await reader(id, [f.root], config, undefined, f.scope);
  assert.equal(value.state.state, 'running');
  assert.equal(value.plan.steps.length, 1);
  assert.equal(value.steps[0].id, 'collect');
  assert.deepEqual(productState, f.snapshot.state);
  assert.equal(value.authority, 'postgresql');
});
