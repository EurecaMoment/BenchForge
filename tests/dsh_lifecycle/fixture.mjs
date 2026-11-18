import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';
import {readFile, writeFile} from 'node:fs/promises';
import {createRequire} from 'node:module';
import path from 'node:path';
import {setTimeout as delay,setImmediate as nextTick} from 'node:timers/promises';
import {DatabaseSync} from 'node:sqlite';
import {invoke} from '../../src/dsh/host/python_tools.mjs';
import {productionProjection} from '../../src/dsh/production/projection.mjs';
import {waitWithSignal} from '../../src/dsh/runtime/cancellation.mjs';

const require=createRequire(path.join(process.env.BENCHFORGE_SMOKE_DSH_ROOT,'package.json'));
const {LlmAdapter,createUserMessage}=await import(require.resolve('@deepseek-ai/dsh-llm'));
const {defineTool}=await import(require.resolve('@deepseek-ai/dsh-tools'));
const request='请实际执行一次 BenchForge 评测数据集生产任务，完整完成五阶段。\nexecution_mode=production；quantity_contract=minimum。\n生产测试数据。';

class Adapter extends LlmAdapter {
  calls=0;
  requests=new Map();
  async resolveModel(provider,model){return {provider,id:model,name:model};}
  async *stream(options){
    this.calls++;
    const attempt=(this.requests.get(options.model)||0)+1;this.requests.set(options.model,attempt);
    if(options.model==='cancel'||options.model.startsWith('review-')&&attempt===1){
      const id='fixture-call',name=options.model==='cancel'?'benchforge_fixture_wait':'benchforge_fixture_reviewed',argumentsJson='{}';
      yield {type:'block-start',index:0,blockType:'tool-call'};
      yield {type:'tool-call-delta',index:0,id,name,argumentsDelta:argumentsJson};
      yield {type:'block-end',index:0,block:{type:'tool-call',id,name,arguments:argumentsJson}};
      yield {type:'finish',reason:{kind:'tool-calls'}};
      return;
    }
    if(options.model==='chat'||options.model.startsWith('review-')){
      yield {type:'block-start',index:0,blockType:'text'};
      yield {type:'text-delta',index:0,text:'普通回答'};
      yield {type:'block-end',index:0,block:{type:'text',text:'普通回答'}};
    }else{
      yield {type:'block-start',index:0,blockType:'reasoning'};
      yield {type:'reasoning-delta',index:0,text:'fixture'};
      yield {type:'block-end',index:0,block:{type:'reasoning',text:'fixture'}};
    }
    yield {type:'finish',reason:{kind:'stop'}};
  }
}

export const name='benchforge-lifecycle-smoke';
export const inject=['llm','agents','tools','subprocess','sessionController','sessionProjections','benchforgeSessions','appReady','appExit'];

export function apply(ctx){
  const adapter=new Adapter();
  ctx.llm.registerAdapter(['lifecycle-fixture'],adapter);
  const active=new Set(),ready=path.join(process.cwd(),'backend-ready.json');
  const reviewCalls=new Map(),reviewEntered=new Map();
  ctx.tools.register(defineTool({name:'benchforge_fixture_reviewed',description:'Record an authorized fixture dispatch.',parameters:{},
    output:{schema:{type:'object',properties:{},additionalProperties:true},render:()=>[]},
    execute:(_args,exec)=>{reviewCalls.set(exec.agent.id,(reviewCalls.get(exec.agent.id)||0)+1);return {accepted:true};}}));
  ctx.on('tools/pre-execute',async(exec,next)=>{
    if(exec.name==='benchforge_fixture_reviewed')reviewEntered.get(exec.agent.id)?.resolve();
    return next();
  });
  ctx.tools.register(defineTool({name:'benchforge_fixture_wait',description:'Wait in the isolated fixture backend.',parameters:{},
    output:{schema:{type:'object',properties:{},additionalProperties:true},render:()=>[]},
    execute:(_args,exec)=>invoke(ctx.subprocess,process.env.BENCHFORGE_SMOKE_PYTHON||'python3',process.cwd(),'',
      'wait',{},exec.signal,{module:'backend',active,environment:{PYTHONPATH:import.meta.dirname,BENCHFORGE_SMOKE_READY:ready}})}));
  const exit=ctx.appExit;
  ctx.effect(()=>ctx.appReady.onReady(()=>{
    void run(ctx,adapter,active,ready,reviewCalls,reviewEntered).then(()=>exit(0),error=>{console.error(error);exit(1);});
  }));
}

async function run(ctx,adapter,active,ready,reviewCalls,reviewEntered){
  const handles=[],result={};
  const create=async model=>{
    const handle=await ctx.agents.create({sessionId:'session-'+randomUUID(),
      agentOptions:{provider:'lifecycle-fixture',model},meta:{cwd:process.cwd()}});
    handles.push(handle);return handle.agent;
  };
  const send=(agent,text)=>agent.followup(createUserMessage({source:{kind:'user'},content:[{type:'text',text}]}));
  const idle=agent=>waitWithSignal(agent.whenIdle(),AbortSignal.timeout(15000));
  const ending=agent=>agent.session.snapshotEvents().findLast(event=>event.type==='turn/end')?.data.reason;
  try{
    const chat=await create('chat');send(chat,'解释阶段审核。');await idle(chat);
    assert.equal(ending(chat)?.kind,'completed');result.ordinaryChat='completed';

    const empty=await create('empty'),before=adapter.calls;send(empty,request);await idle(empty);
    assert.equal(ending(empty)?.kind,'error');
    assert.match(ending(empty).error.message,/任务尚未完成/);
    assert.equal(adapter.calls-before,3);
    let projected=productionProjection.init(empty.session.header);
    for(const event of empty.session.snapshotEvents())projected=productionProjection.apply(projected,event);
    assert.equal(projected.view.state,'incomplete');
    assert.equal(projected.notices.filter(key=>key.includes(':recovery:')).length,2);
    result.emptyResponse={requests:3,state:projected.view.state,ending:ending(empty).kind};

    const cancel=await create('cancel');send(cancel,request);
    const deadline=AbortSignal.timeout(10000);let backend;
    while(!backend){
      try{backend=JSON.parse(await readFile(ready,'utf8'));}
      catch(error){if(error.code!=='ENOENT')throw error;await delay(20,undefined,{signal:deadline});}
    }
    const started=performance.now();
    assert.deepEqual(ctx.sessionController.cancel({sessionId:cancel.id}),{accepted:true});
    await idle(cancel);
    assert.deepEqual(ending(cancel),{kind:'aborted',reason:{kind:'user'}});
    assert.equal(cancel.status,'idle');assert.equal(active.size,0);
    assert.throws(()=>process.kill(backend.pid,0),{code:'ESRCH'});
    result.cancel={ending:ending(cancel).kind,status:cancel.status,childExited:true,elapsedMs:Math.round(performance.now()-started)};

    const passed=await create('review-pass');send(passed,'调用测试工具。');await idle(passed);
    assert.equal(reviewCalls.get(passed.id),1);assert.equal(ending(passed)?.kind,'completed');
    const db=new DatabaseSync(path.join(process.cwd(),'stage-reviews.sqlite'));
    try{
      db.exec('CREATE TABLE reviews(id INTEGER PRIMARY KEY,session TEXT,state TEXT,stage TEXT,deadline REAL,progress TEXT)');
      const locked=await create('review-lock'),entered=Promise.withResolvers();reviewEntered.set(locked.id,entered);
      db.prepare('INSERT INTO reviews VALUES(?,?,?,?,?,?)').run(1,locked.id,'continued','S1',0,'{}');
      db.exec("BEGIN EXCLUSIVE; UPDATE reviews SET state='paused'");
      send(locked,'调用测试工具。');await waitWithSignal(entered.promise,AbortSignal.timeout(5000));
      await nextTick();assert.equal(locked.status,'running');assert.equal(ending(locked),undefined);
      assert.equal(reviewCalls.has(locked.id),false);
      db.exec('COMMIT');await idle(locked);
      assert.equal(reviewCalls.has(locked.id),false);assert.equal(ending(locked)?.kind,'completed');
      const denied=locked.session.snapshotEvents().find(event=>event.type==='tool/result');
      assert.equal(denied?.data.message.isError,true);
      assert.match(JSON.stringify(denied.data),/用户已暂停/);

      const lockedCancel=await create('review-cancel'),reached=Promise.withResolvers();reviewEntered.set(lockedCancel.id,reached);
      db.exec('BEGIN EXCLUSIVE');send(lockedCancel,'调用测试工具。');
      await waitWithSignal(reached.promise,AbortSignal.timeout(5000));
      await nextTick();
      const stopAt=performance.now();ctx.sessionController.cancel({sessionId:lockedCancel.id});await idle(lockedCancel);
      assert.deepEqual(ending(lockedCancel),{kind:'aborted',reason:{kind:'user'}});
      assert.equal(reviewCalls.has(lockedCancel.id),false);
      db.exec('COMMIT');
      result.reviewStorage={allowed:true,pausedDenied:true,stopWhileLockedMs:Math.round(performance.now()-stopAt)};
    }finally{db.close();}
    await writeFile('result.json',JSON.stringify(result,null,2));
    console.log('BENCHFORGE_LIFECYCLE_SMOKE',JSON.stringify(result));
  }finally{
    await Promise.all(handles.map(handle=>handle.dispose()));
  }
}
