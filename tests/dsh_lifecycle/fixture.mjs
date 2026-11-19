import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';
import {readFile, writeFile} from 'node:fs/promises';
import {createRequire} from 'node:module';
import path from 'node:path';
import {setTimeout as delay,setImmediate as nextTick} from 'node:timers/promises';
import {createServer} from 'node:http';
import * as reviewPolicy from '../../src/dsh/production/review_policy.mjs';
import * as examples from '../../src/dsh/examples/host.mjs';
import {createWorkbenchHandler} from '../../src/dsh/dist/workbench.mjs';
import {invoke} from '../../src/dsh/host/python_tools.mjs';
import {productionProjection} from '../../src/dsh/production/projection.mjs';
import {waitWithSignal} from '../../src/dsh/runtime/cancellation.mjs';

const require=createRequire(path.join(process.env.BENCHFORGE_SMOKE_DSH_ROOT,'apps/cli/package.json'));
const {LlmAdapter,createUserMessage}=await import(require.resolve('@deepseek-ai/dsh-llm'));
const {defineTool}=await import(require.resolve('@deepseek-ai/dsh-tools'));
const request='请实际执行一次 BenchForge 评测数据集生产任务，完整完成五阶段。\nexecution_mode=production；quantity_contract=minimum。\n生产测试数据。';

class Adapter extends LlmAdapter {
  calls=0;
  requests=new Map();
  /** 返回生命周期测试所需的确定性模型身份。 */
  async resolveModel(provider,model){return {provider,id:model,name:model};}
  /** 按测试模型和尝试次数生成工具调用或文本流，覆盖取消、复核和清理等原生生命周期路径。 */
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
export const inject=['llm','agents','tools','subprocess','sessionQuery','sessionController','sessionProjections','sessionProjectionCache','benchforgeSessions','workspaceRegistry','sessionPersistence','storageDomain','appReady','appExit'];

/** 装配生命周期烟雾测试的模型、服务、等待工具和可选示例资源。 */
export async function apply(ctx){
  if(process.env.BENCHFORGE_SMOKE_EXAMPLES)
    await ctx.plugin(examples,{enabled:true,sourceDirectory:process.env.BENCHFORGE_SMOKE_EXAMPLES});
  const reviewStates=new Map();
  const server=createServer(async(req,res)=>{
    try {
      let body='';for await(const chunk of req)body+=chunk;
      const {session}=JSON.parse(body);
      const state=await (reviewStates.get(session) || {state:'none'});
      res.setHeader('content-type','application/json');res.end(JSON.stringify(state));
    } catch(error){res.writeHead(500);res.end(JSON.stringify({error:error.message}));}
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const configFile=path.join(process.cwd(),'service.json');
  await writeFile(configFile,JSON.stringify({execution_mode:'exploration',operator_token:'fixture-only',
    service_url:`http://127.0.0.1:${server.address().port}`}));
  ctx.effect(()=>()=>{server.closeAllConnections();server.close();});
  await ctx.plugin(reviewPolicy,{productionConfig:configFile});
  const adapter=new Adapter();
  ctx.llm.registerAdapter(['lifecycle-fixture'],adapter);
  const active=new Set(),ready=path.join(process.cwd(),'backend-ready.json');
  const reviewCalls=new Map(),reviewEntered=new Map();
  ctx.tools.register(defineTool({name:'benchforge_fixture_reviewed',description:'Record an authorized fixture dispatch.',parameters:{},
    output:{schema:{type:'object',properties:{},additionalProperties:true},/** 测试等待工具不需要额外展示内容，返回空渲染块。 */ render:()=>[]},
    /** 执行当前烟雾场景的受控等待或复核操作，并保留原生调用的取消信号。 */
    execute:(_args,exec)=>{reviewCalls.set(exec.agent.id,(reviewCalls.get(exec.agent.id)||0)+1);return {accepted:true};}}));
  ctx.on('tools/pre-execute',async(exec,next)=>{
    if(exec.name==='benchforge_fixture_reviewed')reviewEntered.get(exec.agent.id)?.resolve();
    return next();
  });
  ctx.tools.register(defineTool({name:'benchforge_fixture_wait',description:'Wait in the isolated fixture backend.',parameters:{},
    output:{schema:{type:'object',properties:{},additionalProperties:true},/** 测试等待工具不需要额外展示内容，返回空渲染块。 */ render:()=>[]},
    /** 执行当前烟雾场景的受控等待或复核操作，并保留原生调用的取消信号。 */
    execute:(_args,exec)=>invoke(ctx.subprocess,process.env.BENCHFORGE_SMOKE_PYTHON||'python3',process.cwd(),'',
      'wait',{},exec.signal,{module:'backend',active,environment:{PYTHONPATH:import.meta.dirname,BENCHFORGE_SMOKE_READY:ready}})}));
  const exit=ctx.appExit;
  ctx.effect(()=>ctx.appReady.onReady(()=>{
    void run(ctx,adapter,active,ready,reviewCalls,reviewEntered,reviewStates).then(()=>exit(0),error=>{console.error(error);exit(1);});
  }));
}

/** 驱动真实 Agent 的停止、复核、重载与资源读取场景，汇总生命周期烟雾验证结果。 */
async function run(ctx,adapter,active,ready,reviewCalls,reviewEntered,reviewStates){
  const handles=[],result={};
  /** 为指定测试模型创建原生 Agent，并登记句柄供最终释放。 */
  const create=async model=>{
    const handle=await ctx.agents.create({sessionId:'session-'+randomUUID(),
      agentOptions:{provider:'lifecycle-fixture',model},meta:{cwd:process.cwd()}});
    handles.push(handle);return handle.agent;
  };
  /** 向指定 Agent 发送原生用户跟进消息以启动测试轮次。 */
  const send=(agent,text)=>agent.followup(createUserMessage({source:{kind:'user'},content:[{type:'text',text}]}));
  /** 在有界超时内等待 Agent 原生空闲，防止烟雾测试无限挂起。 */
  const idle=agent=>waitWithSignal(agent.whenIdle(),AbortSignal.timeout(15000));
  /** 读取最近一轮原生结束原因，判断取消或正常完成是否符合预期。 */
  const ending=agent=>agent.session.snapshotEvents().findLast(event=>event.type==='turn/end')?.data.reason;
  try{
    if(process.env.BENCHFORGE_SMOKE_EXAMPLES){
      const example=await ctx.sessionPersistence.stat('session-13742859-5f94-4ca8-ab53-2ffc86ae2e12');
      assert.ok(example);assert.equal(example.header.agentPreset,'benchforge');
      assert.ok(ctx.workspaceRegistry.list().some(workspace=>workspace.path===example.header.cwd));
      const reader=await ctx.sessionPersistence.open(example.header.id,'read');
      try{result.example={events:(await reader.read()).events.length,workspaceRegistered:true};}
      finally{await reader.close();}
      assert.ok(result.example.events>10);
      // A cold sidebar listing must show the title before the user opens the example.
      for(let attempt=0;attempt<100;attempt++){
        const snapshot=ctx.sessionProjectionCache.cachedSnapshot(example.header,['title','sessionListMetadata']);
        if(snapshot?.values.title==='样板 · SpatialForge'){
          assert.equal(snapshot.values.sessionListMetadata.blank,false);
          result.example.sidebarTitle=snapshot.values.title;break;
        }
        await delay(20);
      }
      assert.equal(result.example.sidebarTitle,'样板 · SpatialForge');
      assert.equal(ctx.agents.get(example.header.id),undefined);
      const handler=createWorkbenchHandler({},()=>[{id:'fixture-example',path:example.header.cwd}]);
      /** 通过真实工作台处理器读取示例会话资源，并先断言响应成功。 */
      const get=async parameters=>{
        const response=await handler(new Request('http://fixture/api/benchforge/workbench?'+new URLSearchParams({
          session_id:example.header.id,workspace_path:example.header.cwd,...parameters})));
        assert.equal(response.status,200,await response.clone().text());return response;
      };
      const summary=await (await get({view:'summary'})).json();
      assert.deepEqual(Object.keys(summary.example.stages),['S1','S2','S3','S4','S5']);
      const run=await (await get({view:'run',run:'bundled-example'})).json();
      assert.equal(run.stage_artifacts.filter(row=>row.kind==='image').length,24);
      for(const row of run.stage_artifacts.filter(row=>row.url)){
        const response=await handler(new Request('http://fixture'+row.url));
        assert.equal(response.status,200);assert.ok((await response.arrayBuffer()).byteLength>0);
      }
      const dataset=await (await get({view:'dataset',dataset:summary.datasets[0].id})).json();
      assert.equal(dataset.total,25);
      for(const media of dataset.items.flatMap(item=>item.media||[])){
        const response=await handler(new Request('http://fixture/'+media));
        assert.equal(response.status,200,await response.clone().text());
      }
      result.example.workbench={stages:5,images:24,questions:dataset.total};
    }
    const chat=await create('chat');send(chat,'解释阶段审核。');await idle(chat);
    assert.equal(ending(chat)?.kind,'completed');result.ordinaryChat='completed';
    if(process.env.BENCHFORGE_SMOKE_SEARCH_CONFIG){
      const page=await ctx.sessionQuery.searchEvents({sessionId:chat.id,query:'普通回答'});
      assert.ok(page.items.length>0);assert.equal(page.session.id,chat.id);
      const grouped=await ctx.sessionQuery.searchSessions({query:'普通回答',sessionFilters:[{kind:'id',values:[chat.id]}]});
      assert.equal(grouped.items.length,1);assert.equal(grouped.items[0].header.id,chat.id);
      result.postgresSearch={events:page.items.length,sessions:grouped.items.length};
    }

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
    {
      const locked=await create('review-lock'),entered=Promise.withResolvers();reviewEntered.set(locked.id,entered);
      const delayed=Promise.withResolvers();reviewStates.set(locked.id,delayed.promise);
      send(locked,'调用测试工具。');await waitWithSignal(entered.promise,AbortSignal.timeout(5000));
      await nextTick();assert.equal(locked.status,'running');assert.equal(ending(locked),undefined);
      assert.equal(reviewCalls.has(locked.id),false);
      delayed.resolve({review_id:1,state:'paused',stage:'S1',deadline:0});await idle(locked);
      assert.equal(reviewCalls.has(locked.id),false);assert.equal(ending(locked)?.kind,'completed');
      const denied=locked.session.snapshotEvents().find(event=>event.type==='tool/result');
      assert.equal(denied?.data.message.isError,true);
      assert.match(JSON.stringify(denied.data),/用户已暂停/);

      const lockedCancel=await create('review-cancel'),reached=Promise.withResolvers();reviewEntered.set(lockedCancel.id,reached);
      reviewStates.set(lockedCancel.id,new Promise(()=>{}));send(lockedCancel,'调用测试工具。');
      await waitWithSignal(reached.promise,AbortSignal.timeout(5000));
      await nextTick();
      const stopAt=performance.now();ctx.sessionController.cancel({sessionId:lockedCancel.id});await idle(lockedCancel);
      assert.deepEqual(ending(lockedCancel),{kind:'aborted',reason:{kind:'user'}});
      assert.equal(reviewCalls.has(lockedCancel.id),false);
      result.reviewStorage={allowed:true,pausedDenied:true,stopWhileBackendStalledMs:Math.round(performance.now()-stopAt)};
    }
    await writeFile('result.json',JSON.stringify(result,null,2));
    console.log('BENCHFORGE_LIFECYCLE_SMOKE',JSON.stringify(result));
  }finally{
    await Promise.all(handles.map(handle=>handle.dispose()));
  }
}
