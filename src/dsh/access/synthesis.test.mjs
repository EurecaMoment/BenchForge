import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import test from 'node:test';
import {WorkspaceBindings} from './workspaces.mjs';
import {createPreparationHandler} from '../tools/benchmark/preparation.mjs';
import {createSynthesisSessions} from '../workbench/sessions/synthesis.mjs';

test('preparation starts a native child in its independent root with only frozen explicit inputs', /** 验证受管工作区、可信访问和会话归属在该用例中的具体边界，完成准备、调用和结果断言。用例：preparation starts a native child in its independent root with only frozen explicit inputs。 */ async t => {
  const temp = await fs.mkdtemp(path.join(os.tmpdir(), 'bf-preparation-'));
  t.after(() => fs.rm(temp, {recursive: true, force: true}));
  const rows = new Map(), bindings = new WorkspaceBindings({
    table: {/** 按测试键读取内存记录或模拟服务。 */ get: key => rows.get(key), /** 保存测试记录的副本，模拟持久化写入并防止引用共享。 */ put: async (key, value) => rows.set(key, structuredClone(value))},
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
  await fs.writeFile(path.join(root, 'tasks', 'request.json'), '{"context":{"session_id":"parent"}}');
  const starts = [], services = {
    agents: new Map([['parent', parent]]), benchforgeAccess: bindings,
    llm: {/** 返回用例声明的模型推理能力，验证子任务的能力选择。 */ resolveModelInfo: async () => ({reasoning: {efforts: [{id: 'low'}]}})},
    subagents: {/** 记录原生子会话激活请求，并回显确定的子会话与消息标识。 */ startActivation: async spec => {starts.push(spec); return {childId: spec.childId, messageId: 'message'};}},
  };
  const preparation = createSynthesisSessions({ctx: {/** 按测试键读取内存记录或模拟服务。 */ get: key => services[key]}, workspacePath: root});
  const request = {parentId: 'parent', batchKey: 'one', planPath: plan,
    tasks: [{key: 'task', label: 'Task', prompt: 'Repair input', outputDirectory: output, inputFiles: [draft]}]};
  const handle = createPreparationHandler(() => preparation);
  const result = await handle({workspace: root, plan_path: plan, batch_key: 'one', tasks: request.tasks}, {agent: parent});
  assert.equal(result.state, 'accepted'); assert.equal(starts.length, 1);
  assert.notEqual(starts[0].cwd, root);
  assert.equal(path.dirname(starts[0].cwd), path.dirname(root));
  assert.equal(result.tasks[0].inputSnapshots.length, 2);
  assert.equal(await fs.readFile(result.tasks[0].inputSnapshots[1].path, 'utf8'), 'print(1)');
  assert.ok(starts[0].prompt[0].text.includes(result.tasks[0].childOutputDirectory));
  assert.ok(starts[0].prompt[0].text.includes('benchforge_prepare({"action":"status","batch_key":"one"})'));
  await fs.writeFile(plan, '{"revised":true}');
  assert.equal((await preparation.start(request)).tasks[0].childId, result.tasks[0].childId);
  assert.equal(await fs.readFile(result.tasks[0].inputSnapshots[0].path, 'utf8'), '{"frozen":true}');
  assert.equal(starts.length, 1);
});

async function handoffHarness(t, {status, whenIdle}) {
  const temp = await fs.mkdtemp(path.join(os.tmpdir(), 'bf-preparation-handoff-'));
  t.after(() => fs.rm(temp, {recursive: true, force: true}));
  const rows = new Map(), bindings = new WorkspaceBindings({
    table: {get: key => rows.get(key), put: async (key, value) => rows.set(key, structuredClone(value))},
    hostId: 'host', ownerId: 'owner', workspaceRoot: path.join(temp, 'workspaces'), resourceRoot: path.join(temp, 'resources'),
  });
  await bindings.initialize();
  const root = (await bindings.reserve('parent')).rootPath;
  const parent = {id: 'parent', session: {header: {id: 'parent', cwd: root}}, options: {provider: 'p', model: 'm'},
    status: 'idle', whenIdle: () => Promise.resolve()};
  await bindings.activate(parent.session.header);
  const output = path.join(root, 'tasks', 'delivery');
  await fs.mkdir(output);
  const agents = new Map([['parent', parent]]);
  let childHeader, initialPrompt;
  const services = {
    agents, benchforgeAccess: bindings,
    llm: {resolveModelInfo: async () => ({reasoning: {efforts: [{id: 'low'}]}})},
    subagents: {startActivation: async spec => {
      childHeader = {id: spec.childId, cwd: spec.cwd, parentSession: 'parent', origin: 'subagent'};
      initialPrompt = spec.prompt;
      await bindings.activate(childHeader);
      await fs.writeFile(path.join(spec.cwd, 'tasks', 'preparation', 'manifest.json'), '{"status":"preparing"}');
      agents.set(spec.childId, {id: spec.childId, status, whenIdle});
      return {childId: spec.childId, messageId: 'message'};
    }},
  };
  const factory = workspacePath => createSynthesisSessions({ctx: {get: key => services[key]}, workspacePath});
  const preparation = factory(root);
  const result = await preparation.start({parentId: 'parent', batchKey: 'handoff', tasks: [
    {key: 'task', label: 'Task', prompt: 'Prepare a manifest', outputDirectory: output},
  ]});
  return {preparation, handle: createPreparationHandler(factory), parent, output,
    recordPath: result.recordPath, childId: result.tasks[0].childId, childHeader, initialPrompt, services};
}

test('the parent tool collects a settled child draft using only its existing batch key', async t => {
  const {handle, parent, output, childId} = await handoffHarness(t, {status: 'idle', whenIdle: () => Promise.resolve()});
  const args = {action: 'status', batch_key: 'handoff'};
  const result = await handle(args, {agent: parent});
  assert.equal(result.tasks[0].childId, childId);
  const draft = path.join(output, 'manifest.json');
  assert.deepEqual(result.tasks[0].handoff, {accepted: false, files: [draft]});
  assert.equal(await fs.readFile(draft, 'utf8'), '{"status":"preparing"}');
  await assert.rejects(handle(args, {agent: {...parent, id: 'another-session'}}), /current parent Agent/);
});

test('a cold preparation handoff requires an admitted child, its accepted input and a completed native turn', async t => {
  for (const scenario of ['completed', 'recovered', 'unverified recovery', 'failed', 'interrupted', 'still running', 'queued',
    'empty final turn', 'wrong origin', 'wrong workspace', 'unknown activation', 'unregistered child', 'became live',
    'redirected draft directory']) {
    await t.test(scenario, async t => {
      const {handle, parent, output, childId, childHeader, recordPath, initialPrompt, services} = await handoffHarness(t, {
        status: 'idle', whenIdle: () => Promise.resolve(),
      });
      services.agents.delete(childId);
      if (scenario === 'recovered' || scenario === 'unverified recovery') {
        const record = JSON.parse(await fs.readFile(recordPath, 'utf8'));
        delete record.tasks[0].messageId;
        record.tasks[0].recovered = true;
        await fs.writeFile(recordPath, JSON.stringify(record));
      }
      if (scenario === 'redirected draft directory') {
        const draft = path.join(childHeader.cwd, 'tasks', 'preparation');
        await fs.rename(draft, draft + '.original');
        await fs.symlink(output, draft, 'junction');
      }
      const events = [
        {type: 'turn/start', seq: 1, data: {turn: 1}},
        {type: 'step/start', seq: 2, data: {turn: 1}},
        {type: 'user/message', seq: 3, data: {id: scenario === 'unknown activation' ? 'other' : 'message',
          source: {kind: 'user'}, content: scenario === 'unverified recovery' ? [{type: 'text', text: 'Other work'}] : initialPrompt}},
        {type: 'turn/end', seq: 4, data: {turn: 1, reason: {
          kind: scenario === 'failed' ? 'error' : scenario === 'interrupted' ? 'interrupted' : 'completed',
        }}},
      ];
      if (scenario === 'still running') events.push({type: 'turn/start', seq: 5, data: {turn: 2}});
      if (scenario === 'queued') events.push({type: 'agent/inbox/spliced', seq: 5, data: {inserted: [{id: 'resume'}]}});
      if (scenario === 'empty final turn') events.push({type: 'turn/start', seq: 5, data: {turn: 2}},
        {type: 'turn/end', seq: 6, data: {turn: 2, reason: {kind: 'completed'}}});
      const opened = [], disposed = [];
      services.sessionQuery = {observeSession: async (id, options) => {
        opened.push(id);
        const dispose = () => disposed.push(id);
        if (id === parent.id) return {header: parent.session.header,
          projections: {values: {subagentCatalog: scenario === 'unregistered child' ? [] : [{id: childId, mode: 'continuable'}]}},
          [Symbol.dispose]: dispose};
        assert.equal(id, childId);
        assert.equal(options.projectionMode, 'none');
        if (scenario === 'became live') services.agents.set(childId, {id: childId, status: 'running'});
        const header = {...childHeader};
        if (scenario === 'wrong origin') header.origin = 'user';
        if (scenario === 'wrong workspace') header.cwd = parent.session.header.cwd;
        return {source: 'prepared', header, events, [Symbol.dispose]: dispose};
      }};
      const args = {action: 'status', batch_key: 'handoff'};
      const response = await handle(args, {agent: parent});
      const task = response.tasks[0], manifest = path.join(output, 'manifest.json');
      const ready = scenario === 'completed' || scenario === 'recovered';
      assert.equal(task.nativeActivity.idleConfirmed, ready || scenario === 'redirected draft directory');
      assert.deepEqual(disposed.sort(), opened.sort());
      if (ready) {
        assert.equal(task.nativeActivity.source, 'session_log');
        assert.deepEqual(task.handoff, {accepted: false, files: [manifest]});
        assert.equal(await fs.readFile(manifest, 'utf8'), '{"status":"preparing"}');
        assert.deepEqual((await handle(args, {agent: parent})).tasks[0].handoff, task.handoff);
      } else {
        if (scenario === 'redirected draft directory') assert.match(task.handoff.error, /managed draft directory/);
        else assert.equal(task.handoff, undefined);
        await assert.rejects(fs.stat(manifest), {code: 'ENOENT'});
      }
      if (scenario === 'unregistered child') assert.deepEqual(opened, [parent.id]);
    });
  }
});

test('preparation handoff exports after a settled terminal child status', async t => {
  for (const status of ['inactive', 'completed', 'terminated']) {
    await t.test(status, async t => {
      const harness = await handoffHarness(t, {status, whenIdle: () => Promise.resolve()});
      const result = await harness.preparation.read({parentId: 'parent', batchKey: 'handoff'});
      const handoff = result.tasks[0].handoff;
      assert.equal(result.tasks[0].nativeActivity.idleConfirmed, true);
      assert.deepEqual(handoff.files, [path.join(harness.output, 'manifest.json')]);
      assert.equal(await fs.readFile(handoff.files[0], 'utf8'), '{"status":"preparing"}');
    });
  }
});

test('preparation handoff waits for native idle and rejects running or failed activity', async t => {
  await t.test('unsettled idle promise', async t => {
    let resolveIdle;
    const idle = new Promise(resolve => { resolveIdle = resolve; });
    const harness = await handoffHarness(t, {status: 'inactive', whenIdle: () => idle});
    const pending = await harness.preparation.read({parentId: 'parent', batchKey: 'handoff'});
    assert.equal(pending.tasks[0].nativeActivity.idleConfirmed, false);
    assert.equal(pending.tasks[0].nativeActivity.activityPending, true);
    assert.equal(pending.tasks[0].handoff, undefined);
    assert.equal(await fs.stat(path.join(harness.output, 'manifest.json')).then(() => true, () => false), false);

    resolveIdle();
    const settled = await harness.preparation.read({parentId: 'parent', batchKey: 'handoff'});
    assert.equal(settled.tasks[0].nativeActivity.idleConfirmed, true);
    assert.equal(await fs.readFile(path.join(harness.output, 'manifest.json'), 'utf8'), '{"status":"preparing"}');
  });

  await t.test('running after idle promise settles', async t => {
    const harness = await handoffHarness(t, {status: 'running', whenIdle: () => Promise.resolve()});
    const result = await harness.preparation.read({parentId: 'parent', batchKey: 'handoff'});
    assert.equal(result.tasks[0].nativeActivity.idleConfirmed, false);
    assert.equal(result.tasks[0].nativeActivity.activityPending, true);
    assert.equal(result.tasks[0].handoff, undefined);
  });

  await t.test('idle promise rejects', async t => {
    const harness = await handoffHarness(t, {status: 'terminated', whenIdle: () => Promise.reject(new Error('cancelled'))});
    const result = await harness.preparation.read({parentId: 'parent', batchKey: 'handoff'});
    assert.equal(result.tasks[0].nativeActivity.idleConfirmed, false);
    assert.equal(result.tasks[0].nativeActivity.error.message, 'cancelled');
    assert.equal(result.tasks[0].handoff, undefined);
  });
});
