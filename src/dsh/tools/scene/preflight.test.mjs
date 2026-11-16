import assert from 'node:assert/strict';
import test from 'node:test';
import fs from 'node:fs/promises';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import {validateJsonSchemaValue} from '@deepseek-ai/dsh-tools';
import {HarnessError} from '@deepseek-ai/dsh-llm';
import {apply} from './tools.mjs';
import {reviewDenial} from '../../production/review_policy.mjs';

async function fixture(t) {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'benchforge-preflight-'));
  const requests = [], tools = new Map();
  const state = {status:200, reply:{}};
  const server = http.createServer(async (request, response) => {
    let body = '';
    for await (const chunk of request) body += chunk;
    requests.push({route:request.url, body:JSON.parse(body)});
    response.writeHead(state.status, {'Content-Type':'application/json'});
    response.end(JSON.stringify(state.reply));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(async () => {
    server.closeAllConnections();
    await new Promise(resolve => server.close(resolve));
    const resolved = await fs.realpath(directory);
    assert.equal(path.dirname(resolved), await fs.realpath(os.tmpdir()));
    assert.ok(path.basename(resolved).startsWith('benchforge-preflight-'));
    await fs.rm(resolved, {recursive:true, force:true});
  });
  const config = path.join(directory, 'service.json');
  await fs.writeFile(config, JSON.stringify({
    service_url:'http://127.0.0.1:' + server.address().port, operator_token:'fixture-only',
    execution_mode:'production', artifacts:directory,
  }));
  const context = {
    tools:{register:tool => tools.set(tool.name, tool)}, on(){},
    llm:{}, attachments:{}, benchforgeCapture:{continuation:{}},
    get(name){return this[name];},
  };
  await apply(context, {serviceConfig:config});
  return {requests, state, tool:tools.get('benchforge_scene_preflight')};
}

function report() {
  const issues = Array.from({length:80}, (_, index) => ({
    id:'issue_' + index, code:'missing_support', path:'/objects/' + index + '/support',
    message:'Missing parent ' + index, severity:'error', blocking:true,
  }));
  return {schema:'benchforge.scene-diagnostics/v1', passed:false, status:'blocked',
    issues, skipped_checks:[{check:'geometry', path:'/objects/0', status:'not_evaluated'}],
    repair_groups:[{issue_ids:issues.map(row => row.id), object_ids:['related']}],
    report_path:'/artifacts/scene_preflights/fixture/scene_preflight.json',
    program_snapshot_path:'/artifacts/scene_preflights/fixture/program.json',
    geometry_preview:{objects:[]}, checked:[]};
}

test('preflight preserves every diagnostic and forwards mode through the registered tool', async t => {
  const {requests, state, tool} = await fixture(t);
  state.reply = report();
  assert.equal(tool.isConcurrencySafe({scene_program_path:'/workspace/scene.json'}), true);
  const value = await tool.execute({scene_program_path:'/workspace/scene.json'},
    {signal:new AbortController().signal});
  assert.deepEqual(validateJsonSchemaValue(tool.output.schema, value, ''), []);
  const visible = JSON.parse(tool.output.render({}, value)[0].text);
  assert.deepEqual(visible.issues, state.reply.issues);
  assert.deepEqual(visible.repair_groups, state.reply.repair_groups);
  assert.deepEqual(visible.skipped_checks, state.reply.skipped_checks);
  assert.equal(visible.geometry_preview, undefined);
  assert.deepEqual(value, state.reply);
  assert.deepEqual(requests, [{route:'/scene-preflight',
    body:{scene_program_path:'/workspace/scene.json', execution_mode:'production'}}]);
});

test('HTTP rejection retains the full report instead of only the bounded detail', async t => {
  const {state, tool} = await fixture(t);
  state.status = 400;
  state.reply = {code:'scene_preflight_failed', detail:'summary', preflight:report()};
  await assert.rejects(tool.execute({scene_program_path:'/workspace/scene.json'},
    {signal:new AbortController().signal}), error => {
    assert.ok(error instanceof HarnessError);
    assert.equal(error.code, 'scene_preflight_failed');
    assert.deepEqual(error.response.preflight, state.reply.preflight);
    assert.match(error.message, /Missing parent 79/);
    return true;
  });
});

test('cancellation reaches the request without starting a capture', async t => {
  const {requests, tool} = await fixture(t);
  const controller = new AbortController();
  controller.abort();
  await assert.rejects(tool.execute({scene_program_path:'/workspace/scene.json'},
    {signal:controller.signal}), {name:'AbortError'});
  assert.deepEqual(requests, []);
});

test('pending user review permits static preflight while still blocking capture', () => {
  const waiting = {state:'waiting_user', stage:'S1'};
  assert.equal(reviewDenial({name:'benchforge_scene_preflight'}, waiting, '/workspace'), undefined);
  assert.ok(reviewDenial({name:'benchforge_capture'}, waiting, '/workspace'));
});
