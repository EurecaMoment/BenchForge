import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import {openOwnedFile} from './owned_file.mjs';

test('artifact streaming stays on the approved inode after path replacement', {skip: process.platform !== 'linux'}, /** 验证宿主进程、取消信号和资源清理在该用例中的具体边界，完成准备、调用和结果断言。用例：artifact streaming stays on the approved inode after path replacement。 */ async t => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'benchforge-owned-read-'));
  t.after(() => fs.rm(root, {recursive: true, force: true}));
  const workspace = path.join(root, 'workspace');
  await fs.mkdir(workspace);
  const file = path.join(workspace, 'result.json'), secret = path.join(root, 'secret.json');
  await fs.writeFile(file, 'authorized');
  await fs.writeFile(secret, 'private');
  const {handle} = await openOwnedFile(workspace, file);
  try {
    await fs.rename(file, file + '.old');
    await fs.symlink(secret, file);
    assert.equal(await handle.readFile('utf8'), 'authorized');
    await assert.rejects(openOwnedFile(workspace, file), {status: 403});
  } finally { await handle.close(); }
});

test('artifact reads reject a linked ancestor and an external regular file', {skip: process.platform !== 'linux'}, /** 验证宿主进程、取消信号和资源清理在该用例中的具体边界，完成准备、调用和结果断言。用例：artifact reads reject a linked ancestor and an external regular file。 */ async t => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'benchforge-owned-read-'));
  t.after(() => fs.rm(root, {recursive: true, force: true}));
  const workspace = path.join(root, 'workspace');
  await fs.mkdir(workspace);
  await fs.writeFile(path.join(root, 'secret'), 'private');
  await fs.symlink(root, path.join(workspace, 'linked'));
  await assert.rejects(openOwnedFile(workspace, path.join(workspace, 'linked/secret')), {status: 403});
  await assert.rejects(openOwnedFile(workspace, path.join(root, 'secret')), {status: 403});
});
