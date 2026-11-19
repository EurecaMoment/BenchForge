import assert from 'node:assert/strict';
import test from 'node:test';
import {createWorkbenchState} from '../../ui/workbench/state.js';
import {dictionaries} from '../../ui/workbench/i18n.js';
import {STAGES, stageOfStep} from './stages.mjs';
import {createProgressComponents} from '../../ui/workbench/components/progress.js';
import {pendingCapture} from '../tools/scene/capture/quality_review.mjs';
import {collectionSummary} from './collection.mjs';

const state = createWorkbenchState({STAGES, /** 保留测试场景名称，避免名称转换影响阶段推导断言。 */ sceneName: value => value});
const previews = [
  {stage: 'S2', kind: 'image', task_id: 'scene', revision: 0, file: 'capture/view_0.png'},
  {stage: 'S3', kind: 'json', task_id: 'scene', revision: 0, file: 'capture/view_0.json'},
];

test('both language packs and plan instructions use the shared five-stage meaning', /** 验证生产阶段状态、复核门禁和任务续接在该用例中的具体边界，完成准备、调用和结果断言。用例：both language packs and plan instructions use the shared five-stage meaning。 */ () => {
  assert.equal(STAGES[1].key, 'scenes');
  assert.equal(STAGES[2].key, 'collection');
  assert.equal(dictionaries.zh.collectionNav, '采集');
  assert.equal(dictionaries.en.collectionNav, 'Collection');
  assert.equal(dictionaries.zh.scenes, '场景选取或新建');
  const request = state.productionRequest({mode: 'new', objective: '真实采集', count: 100, capability: ''});
  assert.match(request, /S2 场景选取或新建 → S3 采集/);
  assert.match(request, /S3 采集包含标注/);
});

test('required stage semantics cannot be relabeled by review_stage', /** 验证生产阶段状态、复核门禁和任务续接在该用例中的具体边界，完成准备、调用和结果断言。用例：required stage semantics cannot be relabeled by review_stage。 */ () => {
  assert.equal(stageOfStep({uses: 'collection.validate', review_stage: 'S2'}), 'S3');
  assert.equal(stageOfStep({uses: 'evidence.validate', review_stage: 'S2'}), 'S3');
  assert.equal(stageOfStep({uses: 'templates.produce', review_stage: 'S2'}), 'S4');
  // Capture is composable: a scene preview defaults to S2, while a declared
  // production capture can belong to S3 without changing the tool interface.
  assert.equal(stageOfStep({uses: 'benchforge.capture'}), 'S2');
  assert.equal(stageOfStep({uses: 'benchforge.capture', review_stage: 'S3'}), 'S3');
  assert.equal(state.sceneStage({workflow: 'scene', phase: 'collect'}), 'S3');
});

test('collection counts come from explicit validation receipts and exclude private answers', /** 验证生产阶段状态、复核门禁和任务续接在该用例中的具体边界，完成准备、调用和结果断言。用例：collection counts come from explicit validation receipts and exclude private answers。 */ () => {
  assert.equal(collectionSummary({items: 100, state: 'completed'}), null);
  const summary = collectionSummary({schema: 'benchforge.collection-stage/v1', state: 'needs_evidence',
    required_items: 100, collection: {records: 5, scenes: 1, media_files: 10, native_episodes: 5, native_samples: 500},
    annotations: {items: 5}, answer: 'private'});
  assert.equal(summary.records, 5);
  assert.equal(summary.required, 100);
  assert.equal(summary.episodes, 5);
  assert.equal(summary.state, 'needs_evidence');
  assert.equal(summary.answer, undefined);
});

test('paired scene previews and a completed scene never complete S3 collection', /** 验证生产阶段状态、复核门禁和任务续接在该用例中的具体边界，完成准备、调用和结果断言。用例：paired scene previews and a completed scene never complete S3 collection。 */ () => {
  const pipeline = {steps: [{id: 'scene', uses: 'scene.acceptance', stage: 'S2', state: 'completed'}]};
  const scene = {state: 'SUCCEEDED', tasks: [{workflow: 'scene', state: 'SUCCEEDED'}]};
  assert.equal(state.stageState('S3', pipeline, scene, [], null, previews), 'previewable');
  const annotations = {state: 'SUCCEEDED', tasks: [{phase: 'annotate', state: 'SUCCEEDED'}]};
  assert.equal(state.stageState('S3', pipeline, annotations, [], null, previews), 'previewable');
});

test('only validated collection steps can complete S3; failed validation stays failed', /** 验证生产阶段状态、复核门禁和任务续接在该用例中的具体边界，完成准备、调用和结果断言。用例：only validated collection steps can complete S3; failed validation stays failed。 */ () => {
  for (const uses of ['collection.validate', 'evidence.validate']) {
    const pipeline = {steps: [{uses, stage: 'S3', state: 'completed'}]};
    assert.equal(state.stageState('S3', pipeline, null), 'completed');
    pipeline.steps[0].state = 'needs_evidence';
    assert.equal(state.stageState('S3', pipeline, null, [], null, previews), 'needs_evidence');
  }
  assert.equal(state.stageState('S3', {steps: [{uses: 'benchforge.evidence', stage: 'S3', state: 'completed'}]}, null), 'previewable');
});

test('S3 preview cards do not render a fabricated completion percentage', /** 验证生产阶段状态、复核门禁和任务续接在该用例中的具体边界，完成准备、调用和结果断言。用例：S3 preview cards do not render a fabricated completion percentage。 */ () => {
  const nodes = [];
  const React = {/** 将 React 调用记录为普通节点，供阶段展示测试遍历断言。 */ createElement(type, props, ...children) {const value = {type, props: props || {}, children}; nodes.push(value); return value;}};
  const helpers = {...state, /** 保留测试状态值，便于从组件树检查阶段状态。 */ status: value => value, /** 省略与当前断言无关的表格渲染。 */ table: () => null, /** 保留测试场景名称，避免名称转换影响阶段推导断言。 */ sceneName: value => value};
  const {StageProgress} = createProgressComponents({React, helpers});
  StageProgress({stage: STAGES[2], pipeline: {steps: [{stage: 'S2', state: 'completed'}]},
    scene: null, record: {value: {scenes: {runs: []}}}, artifacts: previews,
    /** 保留或查询测试本地化键，便于断言状态文案。 */
    t: key => dictionaries.zh[key] || key});
  assert.equal(nodes.filter(node => node.type === 'progress').length, 0);
  assert.ok(nodes.some(node => node.props['data-collection-preview']));
});

test('scene quality review still observes a capture whose historical name was corrected', /** 验证生产阶段状态、复核门禁和任务续接在该用例中的具体边界，完成准备、调用和结果断言。用例：scene quality review still observes a capture whose historical name was corrected。 */ () => {
  const receipt = pendingCapture([
    {type: 'turn/start', data: {turn: 1}},
    {type: 'tool/call', data: {callId: 'capture', name: 'benchmarkforge_capture', arguments: '{}'}},
    {type: 'tool/result', data: {meta: {benchforgeInvocation: {resolved: {name: 'benchforge_capture'}}},
      message: {toolCallId: 'capture', content: [{type: 'text', text: '{"run_id":"sf_scene","task_id":"sf_scene.scene0"}'}]}}},
  ], 1);
  assert.equal(receipt.taskId, 'sf_scene.scene0');
});
