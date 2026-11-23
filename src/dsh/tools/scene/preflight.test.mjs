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

/** 建立本地测试服务和工具注册上下文，记录预检请求并允许控制响应状态及诊断。 */
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
    tools:{/** 按名称收集插件注册的工具，供测试直接执行。 */ register:tool => tools.set(tool.name, tool)}, /** 提供测试事件订阅接口，当前用例不需要真实事件总线。 */ on(){},
    llm:{}, attachments:{}, benchforgeCapture:{continuation:{}},
    /** 从测试上下文返回工具插件依赖的服务。 */
    get(name){return this[name];},
  };
  await apply(context, {serviceConfig:config});
  return {requests, state, tools, tool:tools.get('benchforge_scene_preflight'),evidence:tools.get('benchforge_scene_evidence')};
}

test('registered evidence pages preserve task metadata and strip presentation parameters from HTTP',async t=>{
  const {requests,state,evidence}=await fixture(t);
  state.reply={task_id:'sf_one',state:'FAILED_FINAL',revision:0,data:{issues:Array.from({length:60},(_,i)=>'issue '+i)}};
  const args={task_id:'sf_one',file:'scene_review.json',json_pointer:'/issues',offset:20,limit:5};
  const value=await evidence.execute(args,{signal:new AbortController().signal});
  const rendered=JSON.parse(evidence.output.render(args,value)[0].text);
  assert.equal(rendered.task_id,'sf_one');
  assert.equal(rendered.state,'FAILED_FINAL');
  assert.deepEqual(rendered.data,state.reply.data.issues.slice(20,25));
  assert.equal(rendered.report_view.next_offset,25);
  assert.deepEqual(requests[0].body,{task_id:'sf_one',file:'scene_review.json'});
});

/** 生成包含大量阻塞问题的场景诊断，验证完整问题列表及预览裁剪行为。 */
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

test('preflight preserves every diagnostic and forwards mode through the registered tool', /** 验证BenchForge 工具与宿主集成在该用例中的具体边界，完成准备、调用和结果断言。用例：preflight preserves every diagnostic and forwards mode through the registered tool。 */ async t => {
  const {requests, state, tool} = await fixture(t);
  state.reply = report();
  assert.equal(tool.isConcurrencySafe({scene_program_path:'/workspace/scene.json'}), true);
  const value = await tool.execute({scene_program_path:'/workspace/scene.json'},
    {signal:new AbortController().signal});
  assert.deepEqual(validateJsonSchemaValue(tool.output.schema, value, ''), []);
  const overviewText = tool.output.render({}, value)[0].text;
  const overview = JSON.parse(overviewText);
  assert.ok(overviewText.length < 8192);
  assert.equal(overview.report_view.complete, false);
  assert.equal(overview.passed, false);
  assert.equal(overview.report_path, state.reply.report_path);
  assert.equal(overview.issues.total, 80);
  const visible = JSON.parse(tool.output.render({detail:true}, value)[0].text);
  assert.deepEqual(visible.issues, state.reply.issues);
  assert.deepEqual(visible.repair_groups, state.reply.repair_groups);
  assert.deepEqual(visible.skipped_checks, state.reply.skipped_checks);
  assert.equal(visible.geometry_preview, undefined);
  assert.deepEqual(value, state.reply);
  assert.deepEqual(requests, [{route:'/scene-preflight',
    body:{scene_program_path:'/workspace/scene.json', execution_mode:'production'}}]);
});

test('HTTP rejection retains the full report instead of only the bounded detail', /** 验证BenchForge 工具与宿主集成在该用例中的具体边界，完成准备、调用和结果断言。用例：HTTP rejection retains the full report instead of only the bounded detail。 */ async t => {
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

test('capture and retry rejection expose the same structured history to DSH', async t => {
  const {state, tools, requests} = await fixture(t);
  const code = 'S2_CAPTURE_BUDGET_EXHAUSTED';
  const budget = {scene_id:'room',scene_key:'frozen-room',limit:10,used:81,remaining:0,
    evidence:[{task_id:'sf_room.scene0',revision:2,token:'token',accepted:true,captured:true}]};
  state.status = 400;
  state.reply = {code, detail:'summary', issues:[{code,capture_budget:budget}]};
  for (const [name,args] of [
    ['benchforge_capture',{scene_program_path:'/workspace/scene.json'}],
    ['benchforge_retry',{task_id:'sf_room.scene0',capture_mode:'restart'}],
  ]) {
    await assert.rejects(tools.get(name).execute(args, {callId:'fixture-call',agent:{session:{id:'fixture-session'}},
      signal:new AbortController().signal}), error => {
      assert.ok(error instanceof HarnessError);
      assert.equal(error.code, code);
      assert.deepEqual(JSON.parse(error.message).issues[0].capture_budget, budget);
      return true;
    });
  }
  assert.deepEqual(requests.map(row=>row.route), ['/capture','/retry']);
});

test('cancellation reaches the request without starting a capture', /** 验证BenchForge 工具与宿主集成在该用例中的具体边界，完成准备、调用和结果断言。用例：cancellation reaches the request without starting a capture。 */ async t => {
  const {requests, tool} = await fixture(t);
  const controller = new AbortController();
  controller.abort();
  await assert.rejects(tool.execute({scene_program_path:'/workspace/scene.json'},
    {signal:controller.signal}), {name:'AbortError'});
  assert.deepEqual(requests, []);
});

test('pending user review permits static preflight while still blocking capture', /** 验证BenchForge 工具与宿主集成在该用例中的具体边界，完成准备、调用和结果断言。用例：pending user review permits static preflight while still blocking capture。 */ () => {
  const waiting = {state:'waiting_user', stage:'S1'};
  assert.equal(reviewDenial({name:'benchforge_scene_preflight'}, waiting, '/workspace'), undefined);
  assert.ok(reviewDenial({name:'benchforge_capture'}, waiting, '/workspace'));
});
