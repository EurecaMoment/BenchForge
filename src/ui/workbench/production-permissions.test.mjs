import assert from 'node:assert/strict';
import test from 'node:test';
import {createProductionInput, PRODUCTION_SOURCE} from './production-input.js';

/** 构造带指定权限的原生输入与会话夹具，记录生产配置序列化时发起的查询。 */
function composer(permission) {
  const sessionId = 'production-session';
  const demand = {mode: 'new', count: 10, capability: 'spatial', executionMode: 'production', quantityContract: 'exact'};
  const ref = JSON.stringify({version: 1, sessionId, demand});
  const queries = [];
  const state = {draft: '\uFFFCCollect indoor observations', attachmentIds: [],
    occurrences: [{source: PRODUCTION_SOURCE, ref, offset: 0, length: 1}]};
  const session = {projections: {/** 提供当前预设与权限的投影读取器，验证生产入口遵守原生权限。 */ faceOf: (key) => ({/** 按投影键返回 BenchForge 预设或用例指定的权限值。 */ getSnapshot: () =>
    key === 'agentPreset' ? 'benchforge' : {currentValue: permission}})}};
  const ctx = {sessions: {/** 返回当前测试会话的原生绑定。 */ binding: () => ({session}),
    list: {/** 返回包含测试工作目录的会话存储快照。 */ getSnapshot: () => ({byId: {[sessionId]: {cwd: '/workspace'}}})}},
    conversation: {input: {/** 提供当前会话原生输入状态的快照读取器。 */ for: () => ({state: {/** 返回测试输入框当前草稿和配置块状态。 */ getSnapshot: () => state}})}}};
  const {source} = createProductionInput({ctx, /** 保留或查询测试本地化键，便于断言状态文案。 */ t: (key) => key,
    /** 返回固定生产指令，便于断言配置块序列化结果。 */
    productionRequest: () => 'Production request', /** 返回固定场景上下文，检查序列化是否补充约束。 */ productionContext: () => ' with scene policy',
    /** 记录配置序列化所需查询，并返回相应场景选择、计划模板或复核状态。 */
    requestWorkbench: async (query) => {
      queries.push(query);
      return {scene_selection: {allow_new_scenes: true},
        plan_template: {schema: 'benchforge.assessment-template/v1', assessment: {}},
        stage_review: {state: 'idle'}}[query.view];
    }});
  return {/** 调用真实生产配置编码器，验证权限和请求内容校验。 */ serialize: () => source.codec.serialize(ref, new AbortController().signal), queries, state};
}

test('workspace write submits configured production without changing the ordinary draft or permissions', /** 验证工作台数据读取、生产配置和阶段展示在该用例中的具体边界，完成准备、调用和结果断言。用例：workspace write submits configured production without changing the ordinary draft or permissions。 */ async () => {
  const input = composer('workspace-write');
  const before = structuredClone(input.state);
  assert.equal(await input.serialize(), 'Production request with scene policy\n\n');
  assert.deepEqual(input.state, before);
  assert.equal(input.queries.length, 3);
  assert.ok(input.queries.every((query) => query.workspace_path === '/workspace'));
});

test('explicit full access remains usable, while read-only and unresolved permissions cannot submit', /** 验证工作台数据读取、生产配置和阶段展示在该用例中的具体边界，完成准备、调用和结果断言。用例：explicit full access remains usable, while read-only and unresolved permissions cannot submit。 */ async () => {
  await composer('danger-full-access').serialize();
  for (const mode of ['read-only', 'custom', undefined]) {
    const input = composer(mode);
    await assert.rejects(input.serialize(), /permissionsPending/);
    assert.deepEqual(input.queries, []);
  }
});

