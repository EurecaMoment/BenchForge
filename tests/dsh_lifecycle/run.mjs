import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {mkdtemp,readFile,rm} from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';

const dshRoot=path.resolve(process.argv[2]||'third_party/deepseek-harness4BenchForge');
const root=await mkdtemp(path.join(os.tmpdir(),'benchforge-lifecycle-'));
let child,finished,output='';
try{
  child=spawn(process.execPath,[path.join(dshRoot,'apps/cli/lib/bin.js'),'--profile','web',
    '--patch',path.join(import.meta.dirname,'cordis.patch.yml'),'--host','127.0.0.1','--port','0','--no-open'],
  {cwd:root,env:{...process.env,DSH_HOME:path.join(root,'home'),DSH_AGENTS_HOME:path.join(root,'agents'),
    DSH_TELEMETRY_DISABLED:'1',BENCHFORGE_SMOKE_DSH_ROOT:dshRoot},windowsHide:true,stdio:['ignore','pipe','pipe']});
  for(const stream of [child.stdout,child.stderr])stream.on('data',data=>{output=(output+data).slice(-30000);});
  finished=new Promise((resolve,reject)=>{child.once('error',reject);child.once('close',(code,signal)=>resolve({code,signal}));});
  const timeout=setTimeout(()=>child.kill('SIGTERM'),60000);
  const force=setTimeout(()=>child.kill('SIGKILL'),75000);
  let outcome;
  try{outcome=await finished;}finally{clearTimeout(timeout);clearTimeout(force);}
  assert.deepEqual(outcome,{code:0,signal:null},output.replace(/token=[A-Za-z0-9_-]+/g,'token=[hidden]'));
  console.log(await readFile(path.join(root,'result.json'),'utf8'));
}finally{
  if(child?.exitCode===null&&child.signalCode===null)child.kill('SIGKILL');
  if(finished)await finished;
  await rm(root,{recursive:true,force:true});
}
