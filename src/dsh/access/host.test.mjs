import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import {apply} from './host.mjs';
import {currentAccessScope} from './context.mjs';

/** 建立带临时目录、内存存储和事件钩子的访问宿主夹具，便于验证授权创建与释放。 */
async function host(t) {
  const folder = await fs.mkdtemp(path.join(os.tmpdir(), 'bf-access-host-'));
  t.after(() => fs.rm(folder, {recursive: true, force: true}));
  const skill = path.join(folder, 'source-skills', 'test-skill');
  await fs.mkdir(skill, {recursive: true});
  await fs.writeFile(path.join(skill, 'SKILL.md'), '---\nname: test-skill\ndescription: test\n---\nUsage');
  const tables = new Map(), hooks = new Map(), policies = new WeakMap(), active = [];
  const ctx = {
    storageDomain: {/** 提供按表隔离的内存存储域，供宿主插件测试持久化访问绑定。 */ open: async () => ({/** 按表名复用内存记录集，模拟存储域中的独立表。 */ table(name) {
      if (!tables.has(name)) {
        const rows = new Map();
        tables.set(name, {/** 按测试键读取内存记录或模拟服务。 */ get: key => rows.get(key), /** 保存测试记录的副本，模拟持久化写入并防止引用共享。 */ put: async (key, value) => rows.set(key, structuredClone(value))});
      }
      return tables.get(name);
    }, /** 模拟资源关闭，并在用例配置了计数器时记录释放次数。 */ close: async () => {}})},
    workspaceRegistry: {/** 创建测试会话或工作区适配器；会话路径覆盖时沿原生创建钩子与激活钩子执行。 */ create: async () => ({/** 记录工作区与会话的绑定操作。 */ attachSession: async () => {}})},
    sandboxPolicy: {
      /** 返回当前会话的测试权限模式和已登记只读策略。 */
      resolve: ({session}) => ({mode: 'workspace-write', readAccess: policies.get(session)}),
      /** 登记测试只读策略，并返回使旧授权失效的释放函数。 */
      registerReadAccess(session, policy) { policies.set(session, policy); return () => policies.set(session, {...policy, epoch: policy.epoch + 1, roots: []}); },
    },
    /** 按服务名解析测试上下文，活动 Agent 列表由夹具单独提供。 */
    get: name => name === 'agents' ? {/** 返回夹具当前持有的活动 Agent 集合。 */ list: () => active} : ctx[name],
    /** 将插件提供的服务写回测试上下文，供后续断言调用。 */
    provide: (name, value) => {ctx[name] = value;},
    /** 为最小宿主夹具提供资源生命周期接口。 */
    effect: () => {}, /** 按事件名保存插件监听器，供测试显式触发生命周期事件。 */ on: (name, listener) => hooks.set(name, listener), /** 提供测试日志接口，避免依赖真实日志服务。 */ logger: () => ({/** 接受夹具日志警告调用，保持最小宿主接口完整。 */ warn() {}}),
  };
  await apply(ctx, {workspaceRoot: path.join(folder, 'workspaces'), resourceRoot: path.join(folder, 'resources'),
    ownerId: 'owner', readOnlyRoots: [], skillsSource: path.dirname(skill)});
  /** 创建测试会话或工作区适配器；会话路径覆盖时沿原生创建钩子与激活钩子执行。 */
  const create = async (id, parentSession) => {
    const options = {sessionId: id, meta: {agentPreset: 'benchforge', cwd: folder, ...(parentSession ? {parentSession} : {})}};
    const request = {options};
    const prepared = await hooks.get('agent/create')(request, async () => request.options);
    const disposers = [], agent = {id, session: {header: {id, ...prepared.meta}}, ctx: {/** 执行并保存 Agent 资源释放器，供测试确认授权会随会话释放。 */ effect: factory => disposers.push(factory())}};
    await hooks.get('agent/created')({agent});
    active.push(agent);
    return {agent, prepared, disposers};
  };
  return {ctx, hooks, create, policies, folder};
}

test('native creation assigns isolated roots and all tools receive trusted scope', /** 验证受管工作区、可信访问和会话归属在该用例中的具体边界，完成准备、调用和结果断言。用例：native creation assigns isolated roots and all tools receive trusted scope。 */ async t => {
  const {ctx, hooks, create, policies, folder} = await host(t);
  const a = await create('a'), b = await create('b', 'a');
  assert.notEqual(a.prepared.meta.cwd, folder);
  assert.notEqual(a.prepared.meta.cwd, b.prepared.meta.cwd);
  const observed = await hooks.get('tools/execute')({agent: b.agent, arguments: {session_id: 'a'}}, async () => currentAccessScope());
  assert.equal(observed.session_id, 'b');
  assert.equal(observed.parent_session_id, 'a');
  assert.equal(currentAccessScope(), undefined);
  assert.ok(policies.get(a.agent.session).roots.includes(ctx.benchforgeAccess.skillsRoot));
  assert.ok(!policies.get(a.agent.session).roots.includes(folder));
  await assert.rejects(hooks.get('tools/execute')({agent: {session: {header: {id: 'legacy', agentPreset: 'benchforge', cwd: folder}}}}, async () => {}),
    {code: 'BENCHFORGE_WORKSPACE_MIGRATION_REQUIRED'});
});

test('execution generations can advance without changing durable database policy', /** 验证受管工作区、可信访问和会话归属在该用例中的具体边界，完成准备、调用和结果断言。用例：execution generations can advance without changing durable database policy。 */ async t => {
  const {ctx, hooks, create, policies} = await host(t), {agent, disposers} = await create('a');
  disposers[0]();
  const revoked = policies.get(agent.session).epoch;
  await hooks.get('agent/created')({agent});
  assert.ok(policies.get(agent.session).epoch > revoked);
  assert.equal(ctx.benchforgeAccess.scope(agent.session).policy_epoch, 1);
});
