import assert from 'node:assert/strict';
import test from 'node:test';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {DatabaseSync} from 'node:sqlite';
import {readLatestStageReview} from './reviews.mjs';
import {createReviewGuard, reviewDenial} from './review_policy.mjs';

const pending = {stage: 'S1', state: 'waiting_user'};

test('pending reviews block shell, direct simulation, production and delegation routes', () => {
  for (const name of ['bash', 'run_code', 'shell_write', 'shell_start', 'benchforge_task_code',
    'benchforge_capture', 'benchforge_run', 'benchforge_pipeline_run', 'benchforge_prepare',
    'subagent', 'benchforge_model_isaacsim_grpc']) {
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
  assert.equal(reviewDenial({name: 'benchforge_model_isaacsim_grpc', arguments: {action: 'capabilities'}}, pending, '/workspace'), undefined);
  assert.equal(reviewDenial({name: 'write', arguments: {file_path: 'tasks/scene/draft.py'}}, pending, '/workspace'), undefined);
  for (const file of ['stage-reviews.sqlite', 'operations/run/steps/collection/result.json', 'tasks/../../stage-reviews.sqlite']) {
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

test('child operations use the verified parent identity and workspace', () => {
  const observed = [];
  const parent = {id: 'parent', session: {header: {cwd: '/workspace'}}};
  const guard = createReviewGuard({agents: new Map([['parent', parent]]),
    readReview: (workspace, id) => {observed.push([workspace, id]); return pending;}});
  assert.ok(guard({name: 'bash', agent: {id: 'child', session: {header: {cwd: '/workspace/synthesis/child', parentSession: 'parent'}}}}));
  assert.deepEqual(observed, [['/workspace', 'parent']]);
});

test('an unloaded parent cannot hide its review behind a child working directory', () => {
  const guard = createReviewGuard({agents: new Map(), readReview: () => null});
  const agent = {id: 'child', session: {header: {cwd: '/workspace/synthesis/child', parentSession: 'parent'}}};
  assert.ok(guard({name: 'bash', agent}));
  assert.equal(guard({name: 'read', agent}), undefined);
});

test('durable review reader is session-scoped and respects deadline without mutating storage', async t => {
  const folder = await fs.mkdtemp(path.join(os.tmpdir(), 'benchforge-review-'));
  assert.equal(readLatestStageReview(folder, 's1'), null);
  const file = path.join(folder, 'stage-reviews.sqlite'), db = new DatabaseSync(file);
  t.after(async () => {db.close(); await fs.rm(folder, {recursive: true, force: true});});
  db.exec('CREATE TABLE reviews (id INTEGER PRIMARY KEY, session TEXT, key TEXT, stage TEXT, artifact TEXT, deadline REAL, state TEXT, instruction TEXT)');
  db.prepare('INSERT INTO reviews VALUES(?,?,?,?,?,?,?,?)').run(1, 's1', 'a', 'S1', '/plan.json', 100, 'waiting_user', '');
  db.prepare('INSERT INTO reviews VALUES(?,?,?,?,?,?,?,?)').run(2, 'other', 'b', 'S2', '/other.json', 100, 'paused', '');
  assert.equal(readLatestStageReview(folder, 's1', 99999).state, 'waiting_user');
  assert.equal(readLatestStageReview(folder, 's1', 100000).state, 'elapsed');
  assert.equal(db.prepare('SELECT state FROM reviews WHERE id=1').get().state, 'waiting_user');
  assert.equal(readLatestStageReview(folder, 'unrelated'), null);
});
