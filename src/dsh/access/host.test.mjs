import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import {apply} from './host.mjs';
import {currentAccessScope} from './context.mjs';

async function host(t) {
  const folder = await fs.mkdtemp(path.join(os.tmpdir(), 'bf-access-host-'));
  t.after(() => fs.rm(folder, {recursive: true, force: true}));
  const skill = path.join(folder, 'source-skills', 'test-skill');
  await fs.mkdir(skill, {recursive: true});
  await fs.writeFile(path.join(skill, 'SKILL.md'), '---\nname: test-skill\ndescription: test\n---\nUsage');
  const tables = new Map(), hooks = new Map(), policies = new WeakMap(), active = [];
  const ctx = {
    storageDomain: {open: async () => ({table(name) {
      if (!tables.has(name)) {
        const rows = new Map();
        tables.set(name, {get: key => rows.get(key), put: async (key, value) => rows.set(key, structuredClone(value))});
      }
      return tables.get(name);
    }, close: async () => {}})},
    workspaceRegistry: {create: async () => ({attachSession: async () => {}})},
    sandboxPolicy: {
      resolve: ({session}) => ({mode: 'workspace-write', readAccess: policies.get(session)}),
      registerReadAccess(session, policy) { policies.set(session, policy); return () => policies.set(session, {...policy, epoch: policy.epoch + 1, roots: []}); },
    },
    get: name => name === 'agents' ? {list: () => active} : ctx[name],
    provide: (name, value) => {ctx[name] = value;},
    effect: () => {}, on: (name, listener) => hooks.set(name, listener), logger: () => ({warn() {}}),
  };
  await apply(ctx, {workspaceRoot: path.join(folder, 'workspaces'), resourceRoot: path.join(folder, 'resources'),
    ownerId: 'owner', readOnlyRoots: [], skillsSource: path.dirname(skill)});
  const create = async (id, parentSession) => {
    const options = {sessionId: id, meta: {agentPreset: 'benchforge', cwd: folder, ...(parentSession ? {parentSession} : {})}};
    const request = {options};
    const prepared = await hooks.get('agent/create')(request, async () => request.options);
    const disposers = [], agent = {id, session: {header: {id, ...prepared.meta}}, ctx: {effect: factory => disposers.push(factory())}};
    await hooks.get('agent/created')({agent});
    active.push(agent);
    return {agent, prepared, disposers};
  };
  return {ctx, hooks, create, policies, folder};
}

test('native creation assigns isolated roots and all tools receive trusted scope', async t => {
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

test('execution generations can advance without changing durable database policy', async t => {
  const {ctx, hooks, create, policies} = await host(t), {agent, disposers} = await create('a');
  disposers[0]();
  const revoked = policies.get(agent.session).epoch;
  await hooks.get('agent/created')({agent});
  assert.ok(policies.get(agent.session).epoch > revoked);
  assert.equal(ctx.benchforgeAccess.scope(agent.session).policy_epoch, 1);
});
