import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp, mkdir, readFile, rm, symlink, writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {zstdCompressSync} from 'node:zlib';
import {bootstrapExample} from './bootstrap.mjs';

for (const aliased of [false, true]) test('imports a bundled example once' + (aliased ? ' through a home directory alias' : ''), async () => {
  const root = await mkdtemp(path.join(tmpdir(), 'benchforge-example-test-'));
  try {
    const source = path.join(root, 'source'), home = path.join(root, 'home');
    if (aliased) {
      const actualHome = path.join(root, 'actual-home');
      await mkdir(actualHome);
      await symlink(actualHome, home, process.platform === 'win32' ? 'junction' : 'dir');
    }
    const conversation = path.join(source, 'session-test');
    await mkdir(conversation, {recursive: true});
    await writeFile(path.join(source, 'README.md'), 'example');
    const header = {type: 'session', version: 4, id: 'session-example-test', createdAt: 1,
      cwd: '/old/workspace', isSeeded: false, delegationDepth: 0, agentPreset: 'standard'};
    const event = {type: 'permission/preset', seq: 0, time: 2, data: {preset: 'workspace-write'}};
    await writeFile(path.join(conversation, 'session.v4.jsonl.zstd'), Buffer.concat([
      zstdCompressSync(Buffer.from(JSON.stringify(header) + '\n')),
      zstdCompressSync(Buffer.from(JSON.stringify(event) + '\n')),
    ]));
    await writeFile(path.join(source, 'bootstrap.json'), JSON.stringify({
      schema: 'benchforge.example-bootstrap/v1', id: 'spatialforge-v1', sessionId: header.id,
      title: '示例', conversation: 'session-test/session.v4.jsonl.zstd', pathRoots: ['/old/workspace'],
      artifacts: [{path: 'README.md', title: '说明'}],
    }));
    const stored = new Map(), imports = new Map(), table = {get: key => imports.get(key), put: async (key, value) => imports.set(key, value)}, attached = [], handles = [];
    const persistence = {
      stat: async id => stored.get(id),
      create: async meta => {
        const events = [];
        stored.set(meta.id, {header: meta});
        const handle = {header: meta, read: async () => ({events}), append: async batch => events.push(...batch),
          flush: async () => {}, close: async () => {}};
        handles.push(handle); return handle;
      },
      open: async () => { throw new Error('unexpected existing-session open'); },
    };
    const pinned = [];
    const registry = {pinSession: async id => pinned.push(id), create: async (directory, title) => {
      attached.push({directory, title}); return {attachSession: async id => attached.push(id)};
    }};
    const summaries = [], publishSummary = (header, events) => summaries.push({header, events});
    const first = await bootstrapExample({sourceDirectory: source, home, persistence, registry, table, publishSummary});
    assert.equal(first.status, 'imported');
    assert.equal(handles.length, 1);
    assert.equal(imports.get('spatialforge-v1').state, 'complete');
    assert.equal(await readFile(path.join(home, 'examples', 'SpatialForge', 'example', 'SpatialForge', 'README.md'), 'utf8'), 'example');
    const second = await bootstrapExample({sourceDirectory: source, home, persistence, registry, table, publishSummary});
    assert.deepEqual(second, {status: 'existing', sessionId: header.id});
    assert.equal(attached.length, 2);
    assert.deepEqual(pinned, [header.id]);
    assert.equal(summaries.length, 1);
    assert.equal(summaries[0].events.findLast(event => event.type === 'session/title').data.title, '示例');
    const record = {...imports.get('spatialforge-v1')}; delete record.summaryVersion;
    imports.set('spatialforge-v1', record);
    persistence.open = async (id, mode) => {
      assert.equal(id, header.id); assert.equal(mode, 'read'); return handles[0];
    };
    await bootstrapExample({sourceDirectory: source, home, persistence, registry, table, publishSummary});
    assert.equal(summaries.length, 2);
    assert.deepEqual(pinned, [header.id]);
    imports.set('spatialforge-v1', record); stored.clear();
    await bootstrapExample({sourceDirectory: source, home, persistence, registry, table, publishSummary});
    assert.equal(handles.length, 1); assert.equal(summaries.length, 2);
  } finally {
    await rm(root, {recursive: true, force: true});
  }
});
