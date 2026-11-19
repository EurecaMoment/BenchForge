import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {mkdtemp,readFile,rm} from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {setTimeout as delay} from 'node:timers/promises';

const dshRoot=path.resolve(process.argv[2]||'third_party/deepseek-harness4BenchForge');
const root=await mkdtemp(path.join(os.tmpdir(),'benchforge-lifecycle-'));
let child,finished,backend,backendFinished,searchConfig,output='';
try{
  if(process.env.BENCHFORGE_TEST_POSTGRES_URL){
    searchConfig=path.join(root,'search-service.json');
    backend=spawn(process.env.BENCHFORGE_SMOKE_PYTHON||'python3',[path.join(import.meta.dirname,'search_backend.py'),searchConfig],
      {cwd:path.resolve(import.meta.dirname,'../..'),env:{...process.env,
        PYTHONPATH:path.resolve(import.meta.dirname,'../..')+path.delimiter+(process.env.PYTHONPATH||'')},
        stdio:['ignore','pipe','pipe'],windowsHide:true});
    for(const stream of [backend.stdout,backend.stderr])stream.on('data',/** 收集测试服务与宿主的标准输出及错误，只保留最后三万字符供失败诊断。 */ data=>{output=(output+data).slice(-30000);});
    backendFinished=new Promise(/** 将测试子进程的关闭和启动错误转换为可等待结果，确保最终清理有明确退出凭据。 */ resolve=>backend.once('close',resolve));
    for(let attempt=0;;attempt++){
      try{JSON.parse(await readFile(searchConfig,'utf8'));break;}
      catch(error){if(attempt>=100||backend.exitCode!==null)throw Error('Search backend did not start: '+output,{cause:error});await delay(100);}
    }
  }
  child=spawn(process.execPath,[path.join(dshRoot,'apps/cli/lib/bin.js'),'--profile','web',
    '--patch',path.join(import.meta.dirname,'cordis.patch.yml'),'--host','127.0.0.1','--port','0','--no-open'],
  {cwd:root,env:{...process.env,DSH_HOME:path.join(root,'home'),DSH_AGENTS_HOME:path.join(root,'agents'),
    DSH_TELEMETRY_DISABLED:'1',BENCHFORGE_SMOKE_DSH_ROOT:dshRoot,
    ...(searchConfig?{BENCHFORGE_SMOKE_SEARCH_CONFIG:searchConfig}:{})},windowsHide:true,stdio:['ignore','pipe','pipe']});
  for(const stream of [child.stdout,child.stderr])stream.on('data',/** 收集测试服务与宿主的标准输出及错误，只保留最后三万字符供失败诊断。 */ data=>{output=(output+data).slice(-30000);});
  finished=new Promise(/** 将测试子进程的关闭和启动错误转换为可等待结果，确保最终清理有明确退出凭据。 */ (resolve,reject)=>{child.once('error',reject);child.once('close',/** 把宿主退出码和信号保存为结果，供最终生命周期断言。 */ (code,signal)=>resolve({code,signal}));});
  const timeout=setTimeout(/** 在测试时限到达后终止残留宿主；后续强制终止定时器提供退出保证。 */ ()=>child.kill('SIGTERM'),60000);
  const force=setTimeout(/** 在测试时限到达后终止残留宿主；后续强制终止定时器提供退出保证。 */ ()=>child.kill('SIGKILL'),75000);
  let outcome;
  try{outcome=await finished;}finally{clearTimeout(timeout);clearTimeout(force);}
  assert.deepEqual(outcome,{code:0,signal:null},output.replace(/token=[A-Za-z0-9_-]+/g,'token=[hidden]'));
  try {console.log(await readFile(path.join(root,'result.json'),'utf8'));}
  catch(error){throw new Error('The Web fixture did not finish:\n'+output.replace(/token=[A-Za-z0-9_-]+/g,'token=[hidden]'),{cause:error});}
}finally{
  if(child?.exitCode===null&&child.signalCode===null)child.kill('SIGKILL');
  if(finished)await finished;
  if(backend){backend.kill('SIGTERM');await backendFinished;}
  await rm(root,{recursive:true,force:true});
}
