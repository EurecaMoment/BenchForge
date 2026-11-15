import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import {workspaceReference, resolveReadArguments} from './workspace.mjs';
import {canonicalToolName, resolveToolName} from './identifiers.mjs';

async function files(t) {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'benchforge-reference-'));
  t.after(() => fs.rm(directory, {recursive: true, force: true}));
  const cwd = path.join(directory, 'repository', '.deployment', 'node', 'workspace');
  const relative = path.join('tasks', 'case', 'depth.png'), target = path.join(cwd, relative);
  await fs.mkdir(path.dirname(target), {recursive: true});
  await fs.writeFile(target, 'image');
  const typo = path.join(directory, 'repo-typo', '.deployment', 'node', 'workspace', relative);
  return {directory, cwd, relative, target, typo};
}

test('known spellings resolve only to an admitted canonical tool', () => {
  const visible = new Set(['benchforge_pipeline_catalog', 'benchforge_model_isaacsim_grpc']);
  const tools = {executable: name => visible.has(name)};
  for (const name of ['pipeline_catalog', 'benchmarkforge_pipeline_catalog', 'benchmarkforge_model_isaacsim_grpc'])
    assert.equal(resolveToolName(tools, {name}), canonicalToolName(name));
  for (const name of ['benchmarkforge_missing', 'benchforch_pipeline_catalog', 'run', 'bash'])
    assert.equal(resolveToolName(tools, {name}), name);
  visible.delete('benchforge_pipeline_catalog');
  assert.equal(resolveToolName(tools, {name: 'benchmarkforge_pipeline_catalog'}), 'benchmarkforge_pipeline_catalog');
  visible.add('benchmarkforge_pipeline_catalog');
  assert.equal(resolveToolName(tools, {name: 'benchmarkforge_pipeline_catalog'}), 'benchmarkforge_pipeline_catalog');
});

test('a missing ancestor can resolve an exact existing workspace image', async t => {
  const {cwd, relative, target, typo} = await files(t);
  assert.deepEqual(await workspaceReference(typo, cwd), {absolute: target, relative});
  assert.deepEqual(await resolveReadArguments('read_image', {file_path: typo}, cwd), {file_path: target});
  assert.equal(await workspaceReference(relative, cwd), undefined);
  assert.equal(await workspaceReference(target, cwd), undefined);
});

test('no guessing missing filenames, existing external files, traversal or writes', async t => {
  const {cwd, target, typo} = await files(t);
  assert.equal(await workspaceReference(typo.replace('depth.png', 'depth2.png'), cwd), undefined);
  assert.equal(await workspaceReference(typo.replace('case', '..'), cwd), undefined);
  assert.equal(await workspaceReference(typo.replace('workspace', 'other'), cwd), undefined);
  const args = {file_path: typo};
  assert.equal(await resolveReadArguments('write', args, cwd), args);
  await fs.mkdir(path.dirname(typo), {recursive: true});
  await fs.writeFile(typo, 'different image');
  assert.equal(await workspaceReference(typo, cwd), undefined);
  assert.equal(await fs.readFile(target, 'utf8'), 'image');
});

test('workspace reference cannot follow a symlink outside its root', async t => {
  if (process.platform === 'win32') return t.skip('Symlink test runs in the Linux deployment environment');
  const {directory, cwd, target, typo} = await files(t);
  const external = path.join(directory, 'external.png');
  await fs.writeFile(external, 'external');
  await fs.unlink(target);
  await fs.symlink(external, target);
  assert.equal(await workspaceReference(typo, cwd), undefined);
});

test('reference resolution respects cancellation', async t => {
  const {cwd, typo} = await files(t);
  const controller = new AbortController();
  controller.abort();
  await assert.rejects(workspaceReference(typo, cwd, controller.signal), {name: 'AbortError'});
});
