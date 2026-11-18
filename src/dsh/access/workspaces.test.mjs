import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import {WorkspaceBindings} from './workspaces.mjs';
import {currentAccessScope, runWithAccessScope} from './context.mjs';

async function fixture(t) {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'bf-access-'));
  t.after(() => fs.rm(root, {recursive: true, force: true}));
  const rows = new Map(), table = {get: key => rows.get(key), put: async (key, value) => rows.set(key, structuredClone(value))};
  const options = {table, hostId: 'host', ownerId: 'owner', workspaceRoot: path.join(root, 'workspaces'), resourceRoot: path.join(root, 'resources')};
  const store = new WorkspaceBindings(options);
  await store.initialize();
  const create = async (id, parent) => {
    const binding = await store.reserve(id, parent);
    const session = {header: {id, cwd: binding.rootPath, ...(parent ? {parentSession: parent} : {})}};
    await store.activate(session.header);
    return {binding, session};
  };
  return {root, store, options, create};
}

test('concurrent allocation, restart and multi-turn scope keep one stable independent root', async t => {
  const {store, options} = await fixture(t);
  const [first, duplicate] = await Promise.all([store.reserve('a'), store.reserve('a')]);
  assert.equal(first.workspaceId, duplicate.workspaceId);
  const b = await store.reserve('b');
  assert.notEqual(first.rootPath, b.rootPath);
  const resumed = new WorkspaceBindings(options);
  await resumed.initialize();
  assert.deepEqual(await resumed.reserve('a'), first);
  const session = {header: {id: 'a', cwd: first.rootPath}};
  await resumed.activate(session.header);
  assert.equal(resumed.scope(session).workspace_id, first.workspaceId);
  assert.equal(resumed.scope(session).policy_epoch, 1);
  assert.throws(() => resumed.get('old-shared'), {code: 'BENCHFORGE_WORKSPACE_MIGRATION_REQUIRED'});
  await assert.rejects(resumed.activate({id: 'b', cwd: first.rootPath}), {code: 'BENCHFORGE_WORKSPACE_MIGRATION_REQUIRED'});
});

test('child receives immutable explicit inputs, never parent or sibling roots', async t => {
  const {store, create} = await fixture(t), parent = await create('parent'), other = await create('other');
  const source = path.join(parent.binding.rootPath, 'tasks', 'plan.json');
  await fs.writeFile(source, '{"value":1}');
  const child = await store.reserve('child', 'parent');
  assert.equal(path.dirname(child.rootPath), path.dirname(parent.binding.rootPath));
  const snapshot = await store.snapshotInput('child', 'parent', source);
  await fs.writeFile(source, '{"value":2}');
  assert.equal(await fs.readFile(snapshot, 'utf8'), '{"value":1}');
  await assert.rejects(store.snapshotInput('child', 'other', source));
  const privateFile = path.join(other.binding.rootPath, 'tasks', 'private.json');
  await fs.writeFile(privateFile, '{}');
  await assert.rejects(store.snapshotInput('child', 'parent', privateFile));
  await store.activate({id: 'child', cwd: child.rootPath, parentSession: 'parent'});
  assert.equal(await store.snapshotInput('child', 'parent', source), snapshot);
  const newFile = path.join(parent.binding.rootPath, 'tasks', 'new.json');
  await fs.writeFile(newFile, '{}');
  await assert.rejects(store.snapshotInput('child', 'parent', newFile));
});

test('preparation handoff is bounded to drafts and never replaces parent edits', async t => {
  const {store, create} = await fixture(t), parent = await create('parent'), child = await create('child', 'parent');
  const output = path.join(child.binding.rootPath, 'tasks', 'preparation');
  const target = path.join(parent.binding.rootPath, 'tasks', 'handoff');
  await Promise.all([fs.mkdir(output), fs.mkdir(target)]);
  await fs.writeFile(path.join(output, 'draft.py'), 'print(1)');
  const result = await store.exportPreparation('child', 'parent', target);
  assert.equal(result.accepted, false);
  assert.equal(await fs.readFile(path.join(target, 'draft.py'), 'utf8'), 'print(1)');
  await store.exportPreparation('child', 'parent', target);
  await fs.writeFile(path.join(target, 'draft.py'), 'parent edit');
  await assert.rejects(store.exportPreparation('child', 'parent', target), /already differs/);
  await assert.rejects(store.exportPreparation('child', 'parent', path.join(parent.binding.rootPath, 'outputs')), /draft/);
});

test('async scopes remain isolated across parallel tools and are absent afterwards', async () => {
  assert.equal(currentAccessScope(), undefined);
  const results = await Promise.all(['a', 'b'].map(session_id => runWithAccessScope({session_id}, async () => {
    await new Promise(resolve => setTimeout(resolve, 5));
    assert.ok(Object.isFrozen(currentAccessScope()));
    return currentAccessScope().session_id;
  })));
  assert.deepEqual(results, ['a', 'b']);
  assert.equal(currentAccessScope(), undefined);
});

test('separately instantiated plugin modules share the same Host call context', async () => {
  const duplicate = await import('./context.mjs?independent-plugin-copy');
  await runWithAccessScope({session_id: 'shared-host'}, async () => {
    assert.equal(duplicate.currentAccessScope().session_id, 'shared-host');
    await duplicate.runWithAccessScope({session_id: 'nested'}, async () => {
      assert.equal(currentAccessScope().session_id, 'nested');
    });
    assert.equal(currentAccessScope().session_id, 'shared-host');
  });
  assert.equal(duplicate.currentAccessScope(), undefined);
});

test('approved legacy migration copies drafts and grants evidence without changing source history', async t => {
  const {store, root} = await fixture(t), legacy = path.join(root, 'legacy');
  await fs.mkdir(path.join(legacy, 'tasks'), {recursive: true});
  await fs.mkdir(path.join(legacy, 'operations', 'one'), {recursive: true});
  await fs.writeFile(path.join(legacy, 'tasks', 'draft.py'), 'print(1)');
  await fs.writeFile(path.join(legacy, 'operations', 'one', 'image.png'), 'media');
  const header = Object.freeze({id: 'old-session', cwd: legacy});
  await store.reserve('new-session');
  const result = await store.migrateFiles('new-session', header, {cursor: 12}, [
    {relativePath: 'tasks/draft.py', kind: 'draft'},
    {relativePath: 'operations/one/image.png', kind: 'artifact'},
  ]);
  assert.equal(result.productionAuthorityTransferred, false);
  assert.equal(result.migrationFrom.sessionId, 'old-session');
  assert.equal(header.cwd, legacy);
  assert.equal(await fs.readFile(path.join(result.rootPath, 'tasks', 'draft.py'), 'utf8'), 'print(1)');
  assert.equal(result.grants.length, 1);
  await fs.writeFile(path.join(result.rootPath, 'tasks', 'draft.py'), 'new draft');
  assert.equal(await fs.readFile(path.join(legacy, 'tasks', 'draft.py'), 'utf8'), 'print(1)');
  await store.reserve('invalid');
  await assert.rejects(store.migrateFiles('invalid', header, {}, [{relativePath: '../secret', kind: 'artifact'}]));
});
