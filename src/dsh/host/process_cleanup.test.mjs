import assert from 'node:assert/strict';
import test from 'node:test';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {releaseProcess, releaseProcesses} from './process_cleanup.mjs';
import {invoke} from './python_tools.mjs';

/** 构造带终止标记、可控退出等待和固定输出的子进程句柄。 */
function handle(exits) {
  return {done:Promise.resolve({exitCode:0}),/** 标记已向测试进程请求终止，供清理行为断言使用。 */ terminate(){this.terminated=true;},
    waitForExit:exits,collected:{stdout:{/** 返回夹具预设的进程输出片段，供结果读取及错误解析断言。 */ readFrom:()=>({text:'{"state":"ok"}',lossy:false})},stderr:{/** 返回夹具预设的进程输出片段，供结果读取及错误解析断言。 */ readFrom:()=>({text:''})}}};
}

test('an empty managed range releases ownership after termination',/** 验证宿主进程、取消信号和资源清理在该用例中的具体边界，完成准备、调用和结果断言。用例：an empty managed range releases ownership after termination。 */ async()=>{
  const process=handle(async()=>true),active=new Set([process]);
  await releaseProcess(process,{active,timeoutMs:100});
  assert.equal(process.terminated,true);assert.equal(active.size,0);
});

test('stale systemd state cannot hold cleanup forever or publish successful cleanup',/** 验证宿主进程、取消信号和资源清理在该用例中的具体边界，完成准备、调用和结果断言。用例：stale systemd state cannot hold cleanup forever or publish successful cleanup。 */ async()=>{
  const process=handle(()=>new Promise(()=>{})),active=new Set([process]);
  await assert.rejects(releaseProcess(process,{active,timeoutMs:15}),{code:'BENCHFORGE_PROCESS_CLEANUP_PENDING'});
  assert.equal(active.has(process),true);
  process.waitForExit=/** 模拟测试进程已经确认退出，供资源回收成功路径使用。 */ async()=>true;
  await releaseProcesses(active,50);assert.equal(active.size,0);
});

test('a provider that reports an expired wait keeps the handle for cleanup',/** 验证宿主进程、取消信号和资源清理在该用例中的具体边界，完成准备、调用和结果断言。用例：a provider that reports an expired wait keeps the handle for cleanup。 */ async()=>{
  const process=handle(async()=>false),active=new Set([process]);
  await assert.rejects(releaseProcess(process,{active,timeoutMs:100}),{code:'BENCHFORGE_PROCESS_CLEANUP_PENDING'});
  assert.equal(active.has(process),true);
});

test('cancelling a command releases the caller even when command outcome and scope observation stall',/** 验证宿主进程、取消信号和资源清理在该用例中的具体边界，完成准备、调用和结果断言。用例：cancelling a command releases the caller even when command outcome and scope observation stall。 */ async t=>{
  const workspace=await fs.mkdtemp(path.join(os.tmpdir(),'benchforge-cancel-'));
  t.after(()=>fs.rm(workspace,{recursive:true,force:true}));
  const spawned=Promise.withResolvers(),controller=new AbortController(),active=new Set(),reported=[];
  const process=handle(()=>new Promise(()=>{}));process.done=new Promise(()=>{});
  const subprocess={/** 返回固定的测试 Python 路径，避免访问真实解释器发现服务。 */ resolveExecutable:async()=>'/python',/** 提供该用例控制的进程句柄，记录启动并模拟输出、终止和退出等待。 */ spawn(){spawned.resolve();return process;}};
  const operation=invoke(subprocess,'python',workspace,'','status',{},controller.signal,
    {module:'server.tool_api',active,cleanupTimeoutMs:15,/** 收集工具调用期间报告的进程清理异常。 */ onCleanupError:e=>reported.push(e)});
  const stopped=assert.rejects(operation,{name:'AbortError'});
  await spawned.promise;controller.abort(new DOMException('Stopped by the user','AbortError'));await stopped;
  assert.equal(process.terminated,true);assert.equal(active.has(process),true);
  assert.equal(reported[0].code,'BENCHFORGE_PROCESS_CLEANUP_PENDING');
});

test('successful CLI output cannot hide unconfirmed child-process cleanup',/** 验证宿主进程、取消信号和资源清理在该用例中的具体边界，完成准备、调用和结果断言。用例：successful CLI output cannot hide unconfirmed child-process cleanup。 */ async t=>{
  const workspace=await fs.mkdtemp(path.join(os.tmpdir(),'benchforge-cleanup-'));
  t.after(()=>fs.rm(workspace,{recursive:true,force:true}));
  const process=handle(()=>new Promise(()=>{}));
  const subprocess={/** 返回固定的测试 Python 路径，避免访问真实解释器发现服务。 */ resolveExecutable:async()=>'/python',/** 提供该用例控制的进程句柄，记录启动并模拟输出、终止和退出等待。 */ spawn:()=>process};
  await assert.rejects(invoke(subprocess,'python',workspace,'','status',{},undefined,
    {module:'server.tool_api',cleanupTimeoutMs:15}),{code:'BENCHFORGE_PROCESS_CLEANUP_PENDING'});
});
