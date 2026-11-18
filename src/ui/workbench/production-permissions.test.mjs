import assert from 'node:assert/strict';
import test from 'node:test';
import {createProductionInput, PRODUCTION_SOURCE} from './production-input.js';

function composer(permission) {
  const sessionId = 'production-session';
  const demand = {mode: 'new', count: 10, capability: 'spatial', executionMode: 'production', quantityContract: 'exact'};
  const ref = JSON.stringify({version: 1, sessionId, demand});
  const queries = [];
  const state = {draft: '\uFFFCCollect indoor observations', attachmentIds: [],
    occurrences: [{source: PRODUCTION_SOURCE, ref, offset: 0, length: 1}]};
  const session = {projections: {faceOf: (key) => ({getSnapshot: () =>
    key === 'agentPreset' ? 'benchforge' : {currentValue: permission}})}};
  const ctx = {sessions: {binding: () => ({session}),
    list: {getSnapshot: () => ({byId: {[sessionId]: {cwd: '/workspace'}}})}},
    conversation: {input: {for: () => ({state: {getSnapshot: () => state}})}}};
  const {source} = createProductionInput({ctx, t: (key) => key,
    productionRequest: () => 'Production request', productionContext: () => ' with scene policy',
    requestWorkbench: async (query) => {
      queries.push(query);
      return {scene_selection: {allow_new_scenes: true},
        plan_template: {schema: 'benchforge.assessment-template/v1', assessment: {}},
        stage_review: {state: 'idle'}}[query.view];
    }});
  return {serialize: () => source.codec.serialize(ref, new AbortController().signal), queries, state};
}

test('workspace write submits configured production without changing the ordinary draft or permissions', async () => {
  const input = composer('workspace-write');
  const before = structuredClone(input.state);
  assert.equal(await input.serialize(), 'Production request with scene policy\n\n');
  assert.deepEqual(input.state, before);
  assert.equal(input.queries.length, 3);
  assert.ok(input.queries.every((query) => query.workspace_path === '/workspace'));
});

test('explicit full access remains usable, while read-only and unresolved permissions cannot submit', async () => {
  await composer('danger-full-access').serialize();
  for (const mode of ['read-only', 'custom', undefined]) {
    const input = composer(mode);
    await assert.rejects(input.serialize(), /permissionsPending/);
    assert.deepEqual(input.queries, []);
  }
});

