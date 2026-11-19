import {test} from 'node:test';
import assert from 'node:assert/strict';
import {zstdCompressSync} from 'node:zlib';
import {decodeArchive, relocate} from './archive.mjs';

test('decodes and validates a current-format example archive', /** 验证示例资源导入和工作台恢复在该用例中的具体边界，完成准备、调用和结果断言。用例：decodes and validates a current-format example archive。 */ () => {
  const header = {type: 'session', version: 4, id: 'session-example-test', createdAt: 1,
    cwd: '/old/workspace', isSeeded: false, delegationDepth: 0, agentPreset: 'standard'};
  const event = {type: 'permission/preset', seq: 0, time: 2, data: {preset: 'workspace-write'}};
  const bytes = Buffer.concat([zstdCompressSync(Buffer.from(JSON.stringify(header) + '\n')),
    zstdCompressSync(Buffer.from(JSON.stringify(event) + '\n'))]);
  const result = decodeArchive(bytes);
  assert.equal(result.header.id, header.id);
  assert.deepEqual(result.events, [event]);
});

test('relocates archived paths without changing longer path prefixes', /** 验证示例资源导入和工作台恢复在该用例中的具体边界，完成准备、调用和结果断言。用例：relocates archived paths without changing longer path prefixes。 */ () => {
  const source = '/old/workspace';
  assert.equal(relocate(`${source}/a.json ${source}-backup`, [source], '/new/workspace'),
    '/new/workspace/a.json /old/workspace-backup');
  assert.deepEqual(relocate({path: source + '/scene.json'}, [source], '/new/workspace'),
    {path: '/new/workspace/scene.json'});
});
