import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import {artifactProjection} from './projection.mjs';
import {conversationProducts, openConversationArtifact} from './files.mjs';
import {resolveArtifactRoots} from './scope.mjs';

function call(state, name, args, seq = 1) {
  return artifactProjection.apply(state, {type: 'tool/call', seq, data: {name, callId: 'c1', arguments: JSON.stringify(args)}});
}

function result(state, receipt, {seq = 2, error = false, meta} = {}) {
  return artifactProjection.apply(state, {type: 'tool/result', seq, data: {message: {
    source: {kind: 'tool', callId: 'c1'}, toolCallId: 'c1', isError: error,
    content: [{type: 'text', text: JSON.stringify(receipt)}],
  }, ...(meta ? {meta} : {})}});
}

test('production receipts and capture ids are scoped to successful calls', () => {
  let state = artifactProjection.init({cwd: '/tmp/work'});
  state = call(state, 'benchforge_pipeline_plan', {workspace: '/tmp/work'}, 1);
  state = artifactProjection.apply(state, {type: 'turn/end', seq: 2, data: {}});
  state = result(state, {state: 'planned', artifact_dir: '/tmp/work/operations/one', plan: 'operations/one/plan.json'}, {seq: 3});
  assert.deepEqual(state.view.files.map((file) => [file.path, file.stage]), [['/tmp/work/operations/one/plan.json', 'S1']]);
  assert.equal(state.view.primaryWorkspace, '/tmp/work');

  state = call(state, 'benchforge_scene_status', {workspace: '/tmp/work'}, 4);
  state = result(state, {run_id: 'sf_capture_1', state: 'RUNNING'}, {seq: 5});
  assert.deepEqual(state.view.captureIds, ['sf_capture_1']);

  state = call(state, 'benchforge_pipeline_plan', {workspace: '/tmp/work'}, 6);
  const failed = result(state, {plan: '/tmp/work/failed.json'}, {seq: 7, error: true});
  assert.equal(failed.view.files.some((file) => file.path.endsWith('failed.json')), false);
});

test('resolved invocation metadata preserves canonical artifact scope after a typo', () => {
  let state = artifactProjection.init({cwd: '/tmp/work'});
  state = call(state, 'benchmarkforge_pipeline_plan', {workspace: '/tmp/wrok'});
  state = result(state, {state: 'planned', plan: 'operations/one/plan.json'}, {meta: {
    benchforgeInvocation: {requested: {name: 'benchmarkforge_pipeline_plan', arguments: {workspace: '/tmp/wrok'}},
      resolved: {name: 'benchforge_pipeline_plan', arguments: {workspace: '/tmp/work'}}},
  }});
  assert.equal(state.view.primaryWorkspace, '/tmp/work');
  assert.deepEqual(state.view.files.map(file => file.path), ['/tmp/work/operations/one/plan.json']);
});

test('waiting pipeline receipts expose step outputs without inventing missing stages', () => {
  let state = artifactProjection.init({cwd: '/tmp/work'});
  state = call(state, 'benchforge_pipeline_run', {workspace: '/tmp/work'}, 1);
  state = result(state, {state: 'waiting_user', steps: {
    questions: {state: 'completed', result: {
      dataset: '/tmp/work/run/dataset', report: '/tmp/work/run/dataset/quality_report.json',
    }, artifact: {status: 'candidate', result_path: '/tmp/work/run/steps/questions/result.json'}},
    package: {state: 'completed', result: {
      public_bundle: '/tmp/work/run/steps/package/public_bundle.zip',
      authority_bundle: '/tmp/work/run/steps/package/authority_bundle.zip',
    }, artifact: {status: 'candidate', result_path: '/tmp/work/run/steps/package/result.json'}},
  }}, {seq: 2});
  assert.deepEqual(state.view.files.map(({path: file, stage, title}) => [file, stage, title]), [
    ['/tmp/work/run/dataset/quality_report.json', 'S4', '题目质量报告'],
    ['/tmp/work/run/steps/questions/result.json', 'S4', '执行结果'],
    ['/tmp/work/run/steps/package/public_bundle.zip', 'S5', '公开数据包'],
    ['/tmp/work/run/steps/package/authority_bundle.zip', 'S5', '真值数据包'],
    ['/tmp/work/run/steps/package/result.json', 'S5', '执行结果'],
  ]);
  assert.equal(state.view.files.some((file) => ['S2', 'S3'].includes(file.stage)), false);
  assert.equal(Object.hasOwn(state.view, 'state'), false);
});

test('nested task and watch receipts ignore input paths and prose', () => {
  let state = artifactProjection.init({cwd: '/tmp/work'});
  state = call(state, 'benchforge_pipeline_status', {workspace: '/tmp/work'});
  state = result(state, {
    state: 'running',
    context: {image_path: 'input.png'},
    tasks: [{inputs: {image_path: 'source.png'}, result: {output_path: ['outputs/one.json', 'outputs/two.json']}}],
    watch: {outputs: {receipt_path: 'outputs/watch.json'}, message: 'See /tmp/private.txt'},
    catalog: {steps: {questions: {result: {report: 'Example report with exact counts'}}}},
    contracts: {authority_bundle: 'path'},
    steps: {scene: {inputs: {usd_path: 'source.usd'}, result: {request: {image_path: 'requested.png'}}}},
  });
  assert.deepEqual(state.view.files.map((file) => file.path), [
    '/tmp/work/outputs/one.json', '/tmp/work/outputs/two.json', '/tmp/work/outputs/watch.json',
  ]);
});

test('failed pipeline calls do not authorize nested output references', () => {
  const state = call(artifactProjection.init({cwd: '/tmp/work'}), 'benchforge_pipeline_run', {workspace: '/tmp/work'});
  const receipt = {state: 'failed', steps: {questions: {result: {report: 'untrusted.json'}}}};
  assert.deepEqual(result(state, receipt).view.files, []);
  assert.deepEqual(result(state, {...receipt, state: 'completed'}, {error: true}).view.files, []);
});

test('successful direct writes and DSH deliveries become file references', () => {
  let state = artifactProjection.init({cwd: '/tmp/work'});
  state = call(state, 'write', {file_path: 'outputs/questions.json', content: '[]'}, 1);
  state = result(state, {ok: true}, {seq: 2, meta: {path: '/tmp/work/outputs/questions.json'}});
  assert.deepEqual(state.view.files.map((file) => [file.path, file.stage, file.source]),
    [['/tmp/work/outputs/questions.json', null, 'mutation']]);
  state = artifactProjection.apply(state, {type: 'deliverables/presented', seq: 3, data: {
    turn: 1, callId: 'p1', files: [{path: 'outputs/report.md', description: '评测报告'}],
  }});
  assert.deepEqual(state.view.files.at(-1), {
    path: '/tmp/work/outputs/report.md', title: '评测报告', stage: null, source: 'present', seq: 3,
  });
});

test('workspace resolution accepts registered roots and rejects unrelated paths', async (t) => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'benchforge-products-'));
  t.after(() => fs.rm(directory, {recursive: true, force: true}));
  const registered = path.join(directory, 'registered');
  const production = path.join(registered, 'workspace');
  const unrelated = path.join(directory, 'unrelated');
  await Promise.all([fs.mkdir(production, {recursive: true}), fs.mkdir(unrelated)]);
  const roots = await resolveArtifactRoots({}, [{id: 'registered', path: registered}], unrelated, [{
    sessionId: 'session-parent', cwd: unrelated, primaryWorkspace: production, workspaces: [production], captureIds: [],
  }]);
  assert.deepEqual(roots.map((root) => root.path), [registered]);
});

test('artifact reads expose only declared files contained by authorized workspaces', async (t) => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'benchforge-products-'));
  t.after(() => fs.rm(directory, {recursive: true, force: true}));
  const rootPath = path.join(directory, 'workspace');
  await fs.mkdir(rootPath);
  const output = path.join(rootPath, 'questions.json');
  await fs.writeFile(output, '{"items":[]}');
  const roots = [{id: 'workspace', path: rootPath, title: 'workspace'}];
  const evidence = [{sessionId: 'session-parent', revision: 8, files: [
    {path: output, title: '题目数据', stage: 'S4', source: 'tool', seq: 5},
    {path: path.join(directory, 'private.txt'), title: '越界文件', stage: null, source: 'present', seq: 6},
  ]}];
  const products = await conversationProducts({sessionId: 'session-parent', evidence, roots});
  assert.equal(products.total, 2);
  assert.equal(products.items[0].status, 'unavailable');
  assert.equal(products.items[1].status, 'available');
  const request = new Request('http://host/api/artifact');
  const response = await openConversationArtifact(products.items[1].id, {evidence, roots, request});
  assert.equal(response.status, 200);
  assert.equal(await response.text(), '{"items":[]}');
  await assert.rejects(() => openConversationArtifact('arbitrary-path', {evidence, roots, request}), {status: 404});
});
