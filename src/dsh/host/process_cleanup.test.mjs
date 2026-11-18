import assert from 'node:assert/strict';
import test from 'node:test';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {releaseProcess, releaseProcesses} from './process_cleanup.mjs';
import {invoke} from './python_tools.mjs';

function handle(exits) {
  return {done:Promise.resolve({exitCode:0}),terminate(){this.terminated=true;},
    waitForExit:exits,collected:{stdout:{readFrom:()=>({text:'{"state":"ok"}',lossy:false})},stderr:{readFrom:()=>({text:''})}}};
}

test('an empty managed range releases ownership after termination',async()=>{
  const process=handle(async()=>true),active=new Set([process]);
  await releaseProcess(process,{active,timeoutMs:100});
  assert.equal(process.terminated,true);assert.equal(active.size,0);
});

test('stale systemd state cannot hold cleanup forever or publish successful cleanup',async()=>{
  const process=handle(()=>new Promise(()=>{})),active=new Set([process]);
  await assert.rejects(releaseProcess(process,{active,timeoutMs:15}),{code:'BENCHFORGE_PROCESS_CLEANUP_PENDING'});
  assert.equal(active.has(process),true);
  process.waitForExit=async()=>true;
  await releaseProcesses(active,50);assert.equal(active.size,0);
});

test('a provider that reports an expired wait keeps the handle for cleanup',async()=>{
  const process=handle(async()=>false),active=new Set([process]);
  await assert.rejects(releaseProcess(process,{active,timeoutMs:100}),{code:'BENCHFORGE_PROCESS_CLEANUP_PENDING'});
  assert.equal(active.has(process),true);
});

test('cancelling a command releases the caller even when command outcome and scope observation stall',async t=>{
  const workspace=await fs.mkdtemp(path.join(os.tmpdir(),'benchforge-cancel-'));
  t.after(()=>fs.rm(workspace,{recursive:true,force:true}));
  const spawned=Promise.withResolvers(),controller=new AbortController(),active=new Set(),reported=[];
  const process=handle(()=>new Promise(()=>{}));process.done=new Promise(()=>{});
  const subprocess={resolveExecutable:async()=>'/python',spawn(){spawned.resolve();return process;}};
  const operation=invoke(subprocess,'python',workspace,'','status',{},controller.signal,
    {module:'server.tool_api',active,cleanupTimeoutMs:15,onCleanupError:e=>reported.push(e)});
  const stopped=assert.rejects(operation,{name:'AbortError'});
  await spawned.promise;controller.abort(new DOMException('Stopped by the user','AbortError'));await stopped;
  assert.equal(process.terminated,true);assert.equal(active.has(process),true);
  assert.equal(reported[0].code,'BENCHFORGE_PROCESS_CLEANUP_PENDING');
});

test('successful CLI output cannot hide unconfirmed child-process cleanup',async t=>{
  const workspace=await fs.mkdtemp(path.join(os.tmpdir(),'benchforge-cleanup-'));
  t.after(()=>fs.rm(workspace,{recursive:true,force:true}));
  const process=handle(()=>new Promise(()=>{}));
  const subprocess={resolveExecutable:async()=>'/python',spawn:()=>process};
  await assert.rejects(invoke(subprocess,'python',workspace,'','status',{},undefined,
    {module:'server.tool_api',cleanupTimeoutMs:15}),{code:'BENCHFORGE_PROCESS_CLEANUP_PENDING'});
});
