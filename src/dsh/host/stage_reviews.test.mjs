import assert from 'node:assert/strict';
import test from 'node:test';
import {createStageReviewClient} from './stage_reviews.mjs';
import path from 'node:path';
import {pythonHostDirectory} from './python_environment.mjs';

test('host review calls the shared CLI through the subprocess seam and releases its handle',/** 验证宿主进程、取消信号和资源清理在该用例中的具体边界，完成准备、调用和结果断言。用例：host review calls the shared CLI through the subprocess seam and releases its handle。 */ async()=>{
  let spec,terminated=0,reaped=0;
  const subprocess={/** 返回固定的测试 Python 路径，避免访问真实解释器发现服务。 */ resolveExecutable:async()=>'/env/bin/python',/** 提供该用例控制的进程句柄，记录启动并模拟输出、终止和退出等待。 */ spawn:value=>{
    spec=value;
    return {done:Promise.resolve({exitCode:0}),collected:{stdout:{/** 返回夹具预设的进程输出片段，供结果读取及错误解析断言。 */ readFrom:()=>({text:'{"state":"waiting_user","waits_completed":1}',lossy:false})}},
      /** 模拟终止请求；具有计数器的用例同时记录请求次数。 */
      terminate:()=>terminated++,/** 按用例设定确认或延迟进程退出，覆盖回收成功与超时边界。 */ waitForExit:async()=>{reaped++;}};
  }};
  const client=createStageReviewClient(subprocess,{python:'/env/bin/python'});
  const result=await client.advance('/workspace with spaces','session-test',3);
  assert.equal(result.waits_completed,1);
  assert.equal(spec.cwd,pythonHostDirectory);
  assert.equal(spec.argv[spec.argv.indexOf('--workspace') + 1],'/workspace with spaces');
  assert.ok(spec.argv.includes('server.stage_review_cli'));
  assert.deepEqual(JSON.parse(spec.argv.at(-1)),{action:'status',review_id:3});
  assert.equal(terminated,1);assert.equal(reaped,1);
  await client.dispose();
});

test('review config is resolved at Host creation, before changing to the workspace', /** 验证宿主进程、取消信号和资源清理在该用例中的具体边界，完成准备、调用和结果断言。用例：review config is resolved at Host creation, before changing to the workspace。 */ async () => {
  let spec;
  const subprocess = {/** 返回固定的测试 Python 路径，避免访问真实解释器发现服务。 */ resolveExecutable: async () => 'python', /** 提供该用例控制的进程句柄，记录启动并模拟输出、终止和退出等待。 */ spawn: value => {
    spec = value;
    return {done: Promise.resolve({exitCode: 0}),
      collected: {stdout: {/** 返回夹具预设的进程输出片段，供结果读取及错误解析断言。 */ readFrom: () => ({text: '{"state":"waiting_user"}'})}},
      /** 模拟终止请求；具有计数器的用例同时记录请求次数。 */
      terminate() {}, /** 按用例设定确认或延迟进程退出，覆盖回收成功与超时边界。 */ async waitForExit() {}};
  }};
  const client = createStageReviewClient(subprocess, {productionConfig: 'config/instance.json'});
  await client.advance('/another/workspace', 'session-a', 4);
  assert.equal(spec.argv[spec.argv.indexOf('--config') + 1], path.resolve('config/instance.json'));
  await client.dispose();
});

test('unloading the host aborts an in-flight review subprocess before disposal completes',/** 验证宿主进程、取消信号和资源清理在该用例中的具体边界，完成准备、调用和结果断言。用例：unloading the host aborts an in-flight review subprocess before disposal completes。 */ async()=>{
  const spawned=Promise.withResolvers();let terminated=0,reaped=0;
  const subprocess={/** 返回固定的测试 Python 路径，避免访问真实解释器发现服务。 */ resolveExecutable:async()=>'/env/bin/python',/** 提供该用例控制的进程句柄，记录启动并模拟输出、终止和退出等待。 */ spawn:spec=>{
    const done=new Promise((resolve,reject)=>spec.signal.addEventListener('abort',()=>reject(spec.signal.reason),{once:true}));
    spawned.resolve();
    return {done,/** 模拟终止请求；具有计数器的用例同时记录请求次数。 */ terminate:()=>terminated++,/** 按用例设定确认或延迟进程退出，覆盖回收成功与超时边界。 */ waitForExit:async()=>{reaped++;}};
  }};
  const client=createStageReviewClient(subprocess);
  const rejected=assert.rejects(client.advance('/workspace','session-test',1),{name:'AbortError'});
  await spawned.promise;await client.dispose();await rejected;
  assert.ok(terminated>=1);assert.ok(reaped>=1);
});

test('review deadline covers both a stalled subprocess and its stale systemd scope',/** 验证宿主进程、取消信号和资源清理在该用例中的具体边界，完成准备、调用和结果断言。用例：review deadline covers both a stalled subprocess and its stale systemd scope。 */ async()=>{
  const reports=[];
  const subprocess={/** 返回固定的测试 Python 路径，避免访问真实解释器发现服务。 */ resolveExecutable:async()=>'/python',/** 提供该用例控制的进程句柄，记录启动并模拟输出、终止和退出等待。 */ spawn:()=>({done:new Promise(()=>{}),/** 模拟终止请求；具有计数器的用例同时记录请求次数。 */ terminate(){},/** 按用例设定确认或延迟进程退出，覆盖回收成功与超时边界。 */ waitForExit:()=>new Promise(()=>{})})};
  const client=createStageReviewClient(subprocess,{reviewTimeoutMs:15,cleanupTimeoutMs:15,/** 收集子进程清理错误，供宿主卸载与回收断言。 */ onCleanupError:error=>reports.push(error)});
  const keepAlive=setInterval(()=>{},1_000);
  try {
    await assert.rejects(client.advance('/workspace','session-test',1),{name:'TimeoutError'});
    assert.equal(reports[0].code,'BENCHFORGE_PROCESS_CLEANUP_PENDING');
    await assert.rejects(client.dispose(),AggregateError);
  } finally {
    clearInterval(keepAlive);
  }
});

test('user cancellation is propagated to a running background review',/** 验证宿主进程、取消信号和资源清理在该用例中的具体边界，完成准备、调用和结果断言。用例：user cancellation is propagated to a running background review。 */ async()=>{
  const spawned=Promise.withResolvers(),controller=new AbortController();let signal;
  const subprocess={/** 返回固定的测试 Python 路径，避免访问真实解释器发现服务。 */ resolveExecutable:async()=>'/python',/** 提供该用例控制的进程句柄，记录启动并模拟输出、终止和退出等待。 */ spawn:spec=>{
    signal=spec.signal;spawned.resolve();return {done:new Promise(()=>{}),/** 模拟终止请求；具有计数器的用例同时记录请求次数。 */ terminate(){},/** 按用例设定确认或延迟进程退出，覆盖回收成功与超时边界。 */ waitForExit:async()=>true};
  }};
  const client=createStageReviewClient(subprocess),operation=client.advance('/workspace','session-test',1,0,controller.signal);
  const stopped=assert.rejects(operation,{name:'AbortError'});
  await spawned.promise;controller.abort(new DOMException('User stopped','AbortError'));await stopped;
  assert.equal(signal.aborted,true);await client.dispose();
});
