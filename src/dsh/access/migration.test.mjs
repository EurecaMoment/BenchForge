import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import {createWorkspaceMigration} from './migration.mjs';
import {WorkspaceBindings} from './workspaces.mjs';

async function fixture(t, {aliasedRoot = false} = {}) {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'bf-migration-'));
  t.after(() => fs.rm(root, {recursive: true, force: true}));
  let source = path.join(root, 'legacy');
  const sourceId = 'session-legacy';
  const files = ['tasks/plan.json', 'tasks/unattributed.json', 'operations/own/result.json',
    'operations/own/render.png', 'operations/foreign/private.json'];
  for (const file of files) {
    await fs.mkdir(path.dirname(path.join(source, file)), {recursive: true});
    await fs.writeFile(path.join(source, file), file);
  }
  if (aliasedRoot) {
    const alias = path.join(root, 'source-alias');
    await fs.symlink(source, alias, 'junction');
    source = alias;
  }
  const records = new Map();
  const store = new WorkspaceBindings({table: {get: id => records.get(id), put: async (id, row) => records.set(id, structuredClone(row))},
    hostId: 'host', ownerId: 'owner', workspaceRoot: path.join(root, 'workspaces'), resourceRoot: path.join(root, 'resources')});
  await store.initialize();
  const header = Object.freeze({id: sourceId, cwd: source, agentPreset: 'benchforge'});
  const state = {cursor: 10, disposed: 0, files: [
    {path: path.join(source, files[0]), source: 'mutation'},
    {path: path.join(source, files[1]), source: 'present'},
    {path: path.join(source, files[3]), source: 'tool'},
    {path: path.join(source, files[4]), source: 'tool'},
  ]};
  const agents = new Map(), created = [], notices = [];
  const migration = createWorkspaceMigration({
    observe: async () => ({header, cursor: state.cursor, projections: {values: {benchforgeArtifacts: {files: state.files}}},
      [Symbol.dispose]() {state.disposed++;}}),
    readOperations: async (_root, sessionId) => {
      assert.equal(_root, await fs.realpath(source)); assert.equal(sessionId, sourceId);
      return [{session_id: sourceId, state: 'completed', artifact: path.join(source, 'operations/own')},
        {session_id: 'session-foreign', state: 'completed', artifact: path.join(source, 'operations/foreign')}];
    },
    access: {prepareMigration: async ({sourceSessionId, newSessionId, sourceRevision, files}, signal) => {
      assert.equal(sourceSessionId, sourceId);
      await store.reserve(newSessionId);
      return store.migrateFiles(newSessionId, header, sourceRevision, files, signal);
    }},
    sessions: {create: async request => {
      created.push(request);
      await store.activate({id: request.sessionId, cwd: request.cwd, agentPreset: request.agentPreset});
      agents.set(request.sessionId, {inject: notice => notices.push(notice)});
      return {sessionId: request.sessionId};
    }},
    agents, createMessage: value => value,
  });
  return {sourceId, source, header, state, agents, migration, created, notices, store};
}

test('preview intersects formal files with PG ownership and drafts with native successful mutations', async t => {
  const {migration, sourceId, state} = await fixture(t);
  const result = await migration.preview(sourceId);
  assert.deepEqual(result.candidates.map(row => [row.relativePath, row.kind]), [
    ['operations/own/render.png', 'artifact'], ['operations/own/result.json', 'artifact'], ['tasks/plan.json', 'draft'],
  ]);
  assert.equal(result.omitted.filter(row => row.reason === 'ownership_unconfirmed').length, 2);
  assert.equal(state.disposed, 1);
  assert.equal(result.production_authority_transferred, false);
});

test('owner-selected migration uses native create, immutable evidence, writable drafts and logged origin', async t => {
  const {migration, sourceId, source, header, created, notices, store} = await fixture(t);
  const preview = await migration.preview(sourceId);
  const result = await migration.migrate(sourceId, {source_revision: preview.source_revision,
    selected: preview.candidates.map(row => row.id)});
  assert.equal(created.length, 1);
  assert.equal(created[0].agentPreset, 'benchforge');
  assert.equal(header.cwd, source);
  assert.equal(result.history_preserved, true);
  assert.equal(result.production_authority_transferred, false);
  assert.equal(store.get(result.session_id).migrationFrom.sessionId, sourceId);
  assert.equal(notices.length, 1);
  assert.equal(notices[0].source.kind, 'benchforge/workspace-migration');
  for (const file of result.files) assert.equal(typeof file.path, 'string');
  const draft = result.files.find(row => row.kind === 'draft');
  await fs.writeFile(draft.path, 'new');
  assert.equal(await fs.readFile(path.join(source, 'tasks/plan.json'), 'utf8'), 'tasks/plan.json');
  const evidence = result.files.find(row => row.kind === 'artifact');
  if (process.platform !== 'win32') assert.equal((await fs.stat(evidence.path)).mode & 0o222, 0);
});

test('root aliases retain owned candidates without accepting linked task directories', async t => {
  const {migration, sourceId, source, state} = await fixture(t, {aliasedRoot: true});
  await fs.symlink(path.join(source, 'operations/own'), path.join(source, 'tasks/linked'), 'junction');
  state.files.push({path: path.join(source, 'tasks/linked/result.json'), source: 'mutation'});
  const preview = await migration.preview(sourceId);
  assert.deepEqual(preview.candidates.map(row => row.relativePath), [
    'operations/own/render.png', 'operations/own/result.json', 'tasks/plan.json',
  ]);
  assert.ok(preview.omitted.some(row => row.path.endsWith(path.join('linked', 'result.json')) &&
    row.reason === 'not_regular_file'));
  const result = await migration.migrate(sourceId, {source_revision: preview.source_revision,
    selected: preview.candidates.map(row => row.id)});
  assert.equal(result.files.length, 3);
  for (const file of result.files) assert.equal(typeof file.path, 'string');
});

test('stale native revision, changed files and forged manifests cannot migrate', async t => {
  const {migration, sourceId, source, state, created} = await fixture(t);
  const preview = await migration.preview(sourceId);
  const selection = {source_revision: preview.source_revision, selected: preview.candidates.map(row => row.id)};
  state.cursor++;
  await assert.rejects(migration.migrate(sourceId, selection), /conversation changed/);
  state.cursor--;
  await fs.writeFile(path.join(source, 'tasks/plan.json'), 'changed file with a different length');
  await assert.rejects(migration.migrate(sourceId, selection), /selected file changed/);
  await assert.rejects(migration.migrate(sourceId, {...selection, files: ['tasks/private.json']}), /Select candidate IDs/);
  await assert.rejects(migration.migrate(sourceId, {source_revision: preview.source_revision, selected: ['forged']}), /selected file changed/);
  assert.equal(created.length, 0);
});

test('live source runs under native maintenance, running sources are rejected and empty selection starts clean', async t => {
  const {migration, sourceId, agents} = await fixture(t);
  agents.set(sourceId, {status: 'running'});
  await assert.rejects(migration.preview(sourceId), /Stop the source conversation/);
  let maintained = false;
  agents.set(sourceId, {status: 'idle', runMaintenance: async task => {
    maintained = true; return task(new AbortController().signal);
  }});
  const preview = await migration.preview(sourceId);
  const result = await migration.migrate(sourceId, {source_revision: preview.source_revision, selected: []});
  assert.equal(maintained, true);
  assert.deepEqual(result.files, []);
  assert.equal(result.migration_from.sessionId, sourceId);
});

test('failed copy removes staged files and cannot commit a partial migration', async t => {
  const {store, source, header} = await fixture(t);
  const target = await store.reserve('session-failed');
  await assert.rejects(store.migrateFiles(target.sessionId, header, {cursor: 10}, [
    {relativePath: 'tasks/plan.json', kind: 'draft'},
    {relativePath: 'tasks/missing.json', kind: 'draft'},
  ]));
  assert.equal(store.get(target.sessionId).migrationFrom, undefined);
  assert.deepEqual(await fs.readdir(path.join(target.rootPath, 'tasks')), []);
  assert.deepEqual(await fs.readdir(store.resourceRoot), []);
  await assert.rejects(store.migrateFiles(target.sessionId, header, {cursor: 10}, [
    {relativePath: 'tasks/plan.json', kind: 'draft', version: {size: 0, mtimeMs: 0}},
  ]), /changed after owner review/);
  assert.equal(await fs.readFile(path.join(source, 'tasks/plan.json'), 'utf8'), 'tasks/plan.json');
});
