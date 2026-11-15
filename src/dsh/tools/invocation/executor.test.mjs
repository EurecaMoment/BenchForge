import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import test from 'node:test';
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import {Context} from '@deepseek-ai/cordis';
import Tools, {defineTool} from '@deepseek-ai/dsh-tools';
import {BenchForgeExecutor} from './executor.mjs';
import * as Invocation from './executor.mjs';
import {reviewDenial} from '../../production/review_policy.mjs';

// Resolve the services from the installed SDK's own dependency graph so these
// tests exercise the same scoped registry and pipeline as the deployment.
const sdk = createRequire(import.meta.resolve('@deepseek-ai/dsh-tools'));
const {default: SystemPrompt} = await import(sdk.resolve('@deepseek-ai/dsh-system-prompt'));
const {createScope} = await import(sdk.resolve('@deepseek-ai/dsh-scope'));

async function runtime(t) {
  const ctx = new Context();
  const prompt = ctx.plugin(SystemPrompt, {}); await prompt;
  const registry = ctx.plugin(Tools); await registry;
  const plugin = ctx.plugin(Invocation); await plugin;
  t.after(async () => {await plugin.dispose(); await registry.dispose(); await prompt.dispose();});
  const executor = new BenchForgeExecutor(ctx);
  const events = [], invocations = [];
  const register = (name, options = {}) => ctx.tools.register(defineTool({name,
    description: 'Test production operation',
    parameters: {workspace: {type: 'string', required: true}, section: {type: 'string', enum: ['tasks'], required: true}},
    output: {schema: {type: 'object', additionalProperties: true}, render: (_args, value) => [{type: 'text', text: JSON.stringify(value)}]},
    execute: async (args, exec) => {invocations.push({args, name: exec.name}); return {ok: true};},
  }), options);
  const agent = {id: 'test', session: {header: {cwd: '/home/operator/repo/.deployment/node/workspace'},
    append(type, data, options) {const event = {seq: events.length, type, data, ...options}; events.push(event); return event;}}};
  const call = (name, args = {section: 'tasks'}, extra = {}) => ctx.tools.execute({
    calls: [{type: 'tool-call', id: 'call-1', name, arguments: typeof args === 'string' ? args : JSON.stringify(args)}],
    signal: new AbortController().signal, agent, log: {turn: 1, step: 1}, ...extra,
  });
  return {ctx, executor, events, invocations, register, agent, call};
}

test('real native pipeline handles the reported typo once and logs both identities', async t => {
  const {executor, ctx, register, call, invocations, events} = await runtime(t);
  register('benchforge_pipeline_catalog');
  const schema = executor.describe(ctx.tools.definitions()).schemas[0];
  assert.equal(schema.name, 'benchforge_pipeline_catalog');
  assert.equal(schema.parameters.properties.workspace, undefined);
  const {results, additionalContexts} = await call('benchmarkforge_pipeline_catalog');
  assert.equal(results[0].result.isError, false);
  assert.equal(invocations.length, 1);
  assert.equal(invocations[0].name, 'benchforge_pipeline_catalog');
  assert.equal(events[0].data.name, 'benchmarkforge_pipeline_catalog');
  assert.equal(events[1].data.meta.benchforgeInvocation.resolved.name, 'benchforge_pipeline_catalog');
  assert.equal(additionalContexts.length, 1);
});

test('configured gRPC tools share correction without duplicate registrations', async t => {
  const {register, call, invocations, agent} = await runtime(t);
  register('benchforge_model_isaacsim_grpc');
  const result = await call('benchmarkforge_model_isaacsim_grpc', {section: 'tasks', workspace: '/typo/root'});
  assert.equal(result.results[0].result.isError, false);
  assert.deepEqual(invocations, [{name: 'benchforge_model_isaacsim_grpc', args: {section: 'tasks', workspace: agent.session.header.cwd}}]);
});

test('normal calls bind workspace without a repetitive correction notice', async t => {
  const {register, call, invocations} = await runtime(t);
  register('benchforge_pipeline_catalog');
  const result = await call('benchforge_pipeline_catalog');
  assert.equal(result.results[0].result.isError, false);
  assert.equal(result.additionalContexts.length, 0);
  assert.equal(invocations.length, 1);
});

test('corrected calls retain native validation and never execute invalid arguments', async t => {
  const {register, call, invocations} = await runtime(t);
  register('benchforge_pipeline_catalog');
  for (const args of [{section: 'wrong'}, {section: 'tasks', workspace: 42}, '{invalid']) {
    const result = await call('benchmarkforge_pipeline_catalog', args);
    assert.equal(result.results[0].result.isError, true);
  }
  assert.equal(invocations.length, 0);
});

test('canonical guards cannot be bypassed using the misspelled prefix', async t => {
  const {ctx, register, call, invocations} = await runtime(t);
  register('benchforge_pipeline_run');
  ctx.tools.guard(exec => exec.name === 'benchforge_pipeline_run' ? 'Parent only' : undefined);
  const result = await call('benchmarkforge_pipeline_run');
  assert.equal(result.results[0].result.isError, true);
  assert.match(result.results[0].result.content[0].text, /Parent only/);
  assert.equal(invocations.length, 0);
});

test('a plan and a dependent call in the same native batch cannot bypass the review', async t => {
  const {ctx, agent, register, invocations} = await runtime(t);
  let review = null;
  ctx.tools.register(defineTool({name: 'benchforge_pipeline_plan', description: 'Open a review', parameters: {},
    output: {schema: {type: 'object', additionalProperties: true}, render: (_args, value) => [{type: 'text', text: JSON.stringify(value)}]},
    execute: async () => {review = {stage: 'S1', state: 'waiting_user'}; return {saved: true};}}));
  register('benchforge_pipeline_run');
  ctx.tools.guard(exec => reviewDenial(exec, review, '/workspace'));
  const result = await ctx.tools.execute({calls: [
    {type: 'tool-call', id: 'plan', name: 'benchforge_pipeline_plan', arguments: '{}'},
    {type: 'tool-call', id: 'run', name: 'benchmarkforge_pipeline_run', arguments: '{"section":"tasks"}'},
  ], agent, signal: new AbortController().signal, log: {turn: 1, step: 1}});
  assert.equal(result.results[0].result.isError, false);
  assert.equal(result.results[1].result.isError, true);
  assert.equal(invocations.length, 0);
});

test('restrictions and conditional step admission remain authoritative', async t => {
  const {ctx, register, call, invocations, agent} = await runtime(t);
  register('benchforge_pipeline_catalog');
  let scope;
  await ctx.plugin(Object.assign(inner => {scope = createScope(inner, agent);}, {inject: ['tools', 'systemPrompt']}));
  t.after(() => scope.dispose());
  scope.ctx.tools.restrict({deny: ['benchforge_pipeline_catalog']});
  const restricted = await call('benchmarkforge_pipeline_catalog');
  assert.equal(restricted.results[0].result.error.info.code, 'UNKNOWN_TOOL');
  register('benchforge_pipeline_status', {when: () => false});
  const conditional = await call('benchmarkforge_pipeline_status');
  assert.equal(conditional.results[0].result.error.info.code, 'UNKNOWN_TOOL');
  assert.equal(invocations.length, 0);
});

test('unrelated unknown names keep the native error and cancellation runs no body', async t => {
  const {register, call, invocations} = await runtime(t);
  register('benchforge_pipeline_catalog');
  const unknown = await call('benchmarkforge_invented');
  assert.equal(unknown.results[0].result.error.info.code, 'UNKNOWN_TOOL');
  const controller = new AbortController(); controller.abort();
  const aborted = await call('benchmarkforge_pipeline_catalog', {section: 'tasks'}, {signal: controller.signal});
  assert.equal(aborted.results[0].result.isError, true);
  assert.equal(invocations.length, 0);
});

test('image ancestor repair runs through native policy and preserves original call logs', async t => {
  const {ctx, call, agent, events} = await runtime(t);
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'benchforge-executor-'));
  t.after(() => fs.rm(directory, {recursive: true, force: true}));
  const cwd = path.join(directory, 'real', '.deployment', 'node', 'workspace');
  const relative = path.join('tasks', 'sample', 'depth.png');
  const file = path.join(cwd, relative);
  await fs.mkdir(path.dirname(file), {recursive: true}); await fs.writeFile(file, 'native-image-fixture');
  agent.session.header.cwd = cwd;
  const typo = path.join(directory, 'typo', '.deployment', 'node', 'workspace', relative);
  let seen;
  ctx.tools.register(defineTool({name: 'read_image', description: 'Read a real image',
    parameters: {file_path: {type: 'string', required: true}},
    output: {schema: {type: 'string'}, render: (_args, value) => [{type: 'text', text: value}]},
    execute: args => fs.readFile(args.file_path, 'utf8'),
  }));
  ctx.tools.guard(exec => {seen = exec.arguments.file_path;});
  const result = await call('read_image', {file_path: typo});
  assert.equal(result.results[0].result.isError, false);
  assert.equal(result.results[0].result.content[0].text, 'native-image-fixture');
  assert.equal(seen, file);
  assert.equal(JSON.parse(events[0].data.arguments).file_path, typo);
  assert.equal(events[1].data.meta.benchforgeInvocation.resolved.arguments.file_path, file);
});

test('native scheduling keeps batch result order and executes each corrected call once', async t => {
  const {ctx, register, agent, invocations} = await runtime(t);
  register('benchforge_pipeline_catalog');
  const names = ['benchmarkforge_pipeline_catalog', 'benchforge_pipeline_catalog', 'pipeline_catalog'];
  const result = await ctx.tools.execute({signal: new AbortController().signal, agent,
    calls: names.map((name, i) => ({type: 'tool-call', id: 'batch-' + i, name, arguments: '{"section":"tasks"}'})),
    maxParallelToolCalls: 3});
  assert.deepEqual(result.results.map(row => row.callId), ['batch-0', 'batch-1', 'batch-2']);
  assert.ok(result.results.every(row => !row.result.isError));
  assert.equal(invocations.length, 3);
});
