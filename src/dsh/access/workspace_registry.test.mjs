import assert from 'node:assert/strict';
import path from 'node:path';
import test from 'node:test';
import {lookupWorkspaceRoots, retireSessionRegistrations} from './workspace_registry.mjs';

test('retiring private folders preserves user registrations, artifact links, and session scope across retries', async () => {
  const root = path.resolve('instance/workspaces');
  const table = new Map();
  table.put = async (id, row) => table.set(id, row);
  const store = {table, workspaceRoot: root, hostId: 'h', ownerId: 'o'};
  const records = new Map();
  for (const [sessionId, title, members] of [
    ['a', 'BenchForge', ['a']], ['b', 'BenchForge', ['b']], ['c', '我的工作区', ['c']],
    ['d', 'BenchForge', ['d', 'other']], ['empty', 'BenchForge', []], ['pending', 'BenchForge', ['pending']],
  ]) {
    const rootPath = path.join(root, sessionId);
    table.set(sessionId, {sessionId, workspaceId: sessionId, rootPath, hostId: 'h', ownerId: 'o', state: 'active'});
    records.set(sessionId, {id: 'old-' + sessionId, title, path: rootPath, sessionIds: members});
  }
  table.get('pending').state = 'reserved';
  records.set('example', {id: 'example', title: 'BenchForge 示例', path: path.resolve('example'), sessionIds: ['sample']});
  records.set('user', {id: 'user', title: 'BenchForge', path: path.resolve('project'), sessionIds: []});
  let interrupted = false;
  const registry = {list: () => [...records.values()], delete: async id => {
    if (!interrupted && id === 'old-b') { interrupted = true; throw new Error('temporary failure'); }
    for (const [key, row] of records) if (row.id === id) records.delete(key);
  }};
  await assert.rejects(retireSessionRegistrations(store, registry), /temporary failure/);
  await retireSessionRegistrations(store, registry);
  assert.deepEqual([...records.keys()], ['c', 'd', 'empty', 'pending', 'example', 'user']);
  assert.equal(table.get('a').artifactRootId, 'old-a');
  assert.equal(table.get('b').artifactRootId, 'old-b');
  assert.deepEqual(lookupWorkspaceRoots(store, registry, ['a']).map(row => row.id), ['old-a', 'example', 'user']);
  assert.deepEqual(lookupWorkspaceRoots(store, registry, ['c']).map(row => row.id), ['old-c', 'example', 'user']);
  assert.deepEqual(lookupWorkspaceRoots(store, registry, ['unbound'], [path.join(root, 'b')]).map(row => row.id),
    ['example', 'user']);
});
