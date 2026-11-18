import assert from 'node:assert/strict';
import test from 'node:test';
import {createReviewPolicy, reviewDenial} from './review_policy.mjs';

const pending = {stage: 'S1', state: 'waiting_user'};

test('pending reviews block shell, direct simulation, production and delegation routes', () => {
  for (const name of ['bash', 'run_code', 'shell_write', 'shell_start', 'benchforge_task_code',
    'benchforge_capture', 'benchforge_run', 'benchforge_pipeline_run', 'benchforge_prepare',
    'subagent', 'benchforge_isaac_sim']) {
    assert.equal(reviewDenial({name, arguments: {action: 'invoke'}}, pending, '/workspace')?.info.code, 'STAGE_REVIEW_PENDING', name);
  }
  assert.ok(reviewDenial({name: 'benchforge_stage_review', arguments: {action: 'continue'}}, pending, '/workspace'));
  assert.ok(reviewDenial({name: 'benchforge_stage_review', arguments: {action: 'instruction'}}, pending, '/workspace'));
});

test('reads, status, previews, validated plans and bounded draft edits remain usable', () => {
  for (const name of ['read', 'read_image', 'glob', 'grep', 'present', 'benchforge_pipeline_catalog',
    'benchforge_scene_evidence', 'benchforge_wait', 'benchforge_pipeline_plan']) {
    assert.equal(reviewDenial({name}, pending, '/workspace'), undefined, name);
  }
  assert.equal(reviewDenial({name: 'benchforge_isaac_sim', arguments: {action: 'capabilities'}}, pending, '/workspace'), undefined);
  assert.equal(reviewDenial({name: 'write', arguments: {file_path: 'tasks/scene/draft.py'}}, pending, '/workspace'), undefined);
  for (const file of ['config/service.json', 'operations/run/steps/collection/result.json', 'tasks/../../config/service.json']) {
    assert.ok(reviewDenial({name: 'write', arguments: {file_path: file}}, pending, '/workspace'));
  }
});

test('released reviews and ordinary chats do not reduce tool concurrency or capability', () => {
  for (const review of [null, {state: 'elapsed'}, {state: 'continued'}]) {
    assert.equal(reviewDenial({name: 'bash'}, review, '/workspace'), undefined);
  }
  assert.ok(reviewDenial({name: 'bash'}, {stage: 'S2', state: 'paused'}, '/workspace'));
  assert.ok(reviewDenial({name: 'bash'}, {stage: 'S2', state: 'needs_input'}, '/workspace'));
});

test('child operations use the verified parent identity and workspace', async () => {
  const observed = [];
  const parent = {id: 'parent', session: {header: {cwd: '/workspace'}}};
  const guard = createReviewPolicy({agents: new Map([['parent', parent]]),
    readReview: (workspace, id) => {observed.push([workspace, id]); return pending;}});
  assert.ok(await guard({name: 'bash', agent: {id: 'child', session: {header: {cwd: '/workspace/synthesis/child', parentSession: 'parent'}}}}));
  assert.deepEqual(observed, [['/workspace', 'parent']]);
});

test('an unloaded parent cannot hide its review behind a child working directory', async () => {
  const guard = createReviewPolicy({agents: new Map(), readReview: () => null});
  const agent = {id: 'child', session: {header: {cwd: '/workspace/synthesis/child', parentSession: 'parent'}}};
  assert.ok(await guard({name: 'bash', agent}));
  assert.equal(await guard({name: 'read', agent}), undefined);
});
