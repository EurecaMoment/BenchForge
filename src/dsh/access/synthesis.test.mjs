import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import test from 'node:test';
import {WorkspaceBindings} from './workspaces.mjs';
import {createSynthesisSessions} from '../workbench/sessions/synthesis.mjs';

test('preparation starts a native child in its independent root with only frozen explicit inputs', async t => {
  const temp = await fs.mkdtemp(path.join(os.tmpdir(), 'bf-preparation-'));
  t.after(() => fs.rm(temp, {recursive: true, force: true}));
  const rows = new Map(), bindings = new WorkspaceBindings({
    table: {get: key => rows.get(key), put: async (key, value) => rows.set(key, structuredClone(value))},
    hostId: 'host', ownerId: 'owner', workspaceRoot: path.join(temp, 'workspaces'), resourceRoot: path.join(temp, 'resources'),
  });
  await bindings.initialize();
  const root = (await bindings.reserve('parent')).rootPath;
  const parent = {id: 'parent', session: {header: {id: 'parent', cwd: root}}, options: {provider: 'p', model: 'm'}};
  await bindings.activate(parent.session.header);
  const plan = path.join(root, 'tasks', 'plan.json'), draft = path.join(root, 'tasks', 'input.py');
  const output = path.join(root, 'tasks', 'delivery');
  await fs.mkdir(output);
  await fs.writeFile(plan, '{"frozen":true}'); await fs.writeFile(draft, 'print(1)');
  const starts = [], services = {
    agents: new Map([['parent', parent]]), benchforgeAccess: bindings,
    llm: {resolveModelInfo: async () => ({reasoning: {efforts: [{id: 'low'}]}})},
    subagents: {startActivation: async spec => {starts.push(spec); return {childId: spec.childId, messageId: 'message'};}},
  };
  const preparation = createSynthesisSessions({ctx: {get: key => services[key]}, workspacePath: root});
  const request = {parentId: 'parent', batchKey: 'one', planPath: plan,
    tasks: [{key: 'task', label: 'Task', prompt: 'Repair input', outputDirectory: output, inputFiles: [draft]}]};
  const result = await preparation.start(request);
  assert.equal(result.state, 'accepted'); assert.equal(starts.length, 1);
  assert.notEqual(starts[0].cwd, root);
  assert.equal(path.dirname(starts[0].cwd), path.dirname(root));
  assert.equal(result.tasks[0].inputSnapshots.length, 2);
  assert.equal(await fs.readFile(result.tasks[0].inputSnapshots[1].path, 'utf8'), 'print(1)');
  assert.ok(starts[0].prompt[0].text.includes(result.tasks[0].childOutputDirectory));
  assert.equal((await preparation.start(request)).tasks[0].childId, result.tasks[0].childId);
  assert.equal(starts.length, 1);
});
