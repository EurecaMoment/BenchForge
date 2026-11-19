import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp, mkdir, readFile, rm, symlink, writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {zstdCompressSync} from 'node:zlib';
import {bootstrapExample} from './bootstrap.mjs';

for (const aliased of [false, true]) test('imports a bundled example once' + (aliased ? ' through a home directory alias' : ''), /** 分别以真实主目录和目录别名验证示例只导入一次，恢复后复用会话与工作台数据。 */ async () => {
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
    const stored = new Map(), imports = new Map(), table = {/** 按测试键读取内存记录或模拟服务。 */ get: key => imports.get(key), /** 保存测试记录的副本，模拟持久化写入并防止引用共享。 */ put: async (key, value) => imports.set(key, value)}, attached = [], handles = [];
    const persistence = {
      /** 读取测试已持久化的会话记录。 */
      stat: async id => stored.get(id),
      /** 创建并记录测试会话句柄或工作区绑定，供示例导入及幂等恢复断言。 */
      create: async meta => {
        const events = [];
        stored.set(meta.id, {header: meta});
        const handle = {header: meta, /** 返回当前测试句柄保存的事件列表。 */ read: async () => ({events}), /** 把事件批次加入测试会话，保留原生追加顺序。 */ append: async batch => events.push(...batch),
          /** 模拟会话事件已刷新，无需真实磁盘写入。 */
          flush: async () => {}, /** 模拟资源关闭，并在用例配置了计数器时记录释放次数。 */ close: async () => {}};
        handles.push(handle); return handle;
      },
      /** 提供用例配置的存储或会话打开行为，并记录资源生命周期。 */
      open: async () => { throw new Error('unexpected existing-session open'); },
    };
    const pinned = [];
    const registry = {/** 记录被置顶的示例会话标识。 */ pinSession: async id => pinned.push(id), /** 创建并记录测试会话句柄或工作区绑定，供示例导入及幂等恢复断言。 */ create: async (directory, title) => {
      attached.push({directory, title}); return {/** 记录工作区与会话的绑定操作。 */ attachSession: async id => attached.push(id)};
    }};
    const summaries = [], publishSummary = /** 记录示例会话头及事件，用于检查冷投影摘要发布。 */ (header, events) => summaries.push({header, events});
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
    persistence.open = /** 断言示例会话按只读方式打开，并返回已登记的测试句柄。 */ async (id, mode) => {
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
