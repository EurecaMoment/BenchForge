import assert from 'node:assert/strict';
import test from 'node:test';
import {setImmediate as nextTick} from 'node:timers/promises';
import {createStageReviewReader} from './reviews.mjs';
import {createReviewPolicy} from './review_policy.mjs';

test('a refused review connection recovers and a newly paused review still blocks production',async()=>{
  let calls=0;
  const reader=createStageReviewReader({service:async()=>{
    if(++calls===1)throw new TypeError('fetch failed',{cause:Object.assign(new Error(),{code:'ECONNREFUSED'})});
    return {review_id:3,state:'paused',stage:'S2'};
  }});
  const policy=createReviewPolicy({readReview:reader.latest});
  const denial=await policy({name:'bash',agent:{id:'s',session:{header:{cwd:'/workspace'}}}});
  assert.equal(calls,2);
  assert.equal(denial.info.code,'STAGE_REVIEW_PENDING');
});

test('each dispatch sees committed backend state, without caching prior approval',/** 验证生产阶段状态、复核门禁和任务续接在该用例中的具体边界，完成准备、调用和结果断言。用例：each dispatch sees committed backend state, without caching prior approval。 */ async()=>{
  const requests=[];let state='continued';
  const reader=createStageReviewReader({/** 提供用例控制的服务响应，覆盖正常返回、超时和取消等读取边界。 */ service:async(route,args)=>{
    requests.push({route,args});return {review_id:12,state,deadline:0};
  }});
  assert.equal((await reader.byId('/workspace','session-test',1)).review_id,12);
  state='paused';
  const policy=createReviewPolicy({readReview:reader.latest});
  assert.ok(await policy({name:'bash',agent:{id:'session-test',session:{header:{cwd:'/workspace'}}}}));
  assert.deepEqual(requests.map(value=>value.route),['/stage-review/read','/stage-review/read']);
  assert.deepEqual(requests[0].args,{workspace:'/workspace',session:'session-test',review_id:1});
});

test('one stalled reader can be stopped without delaying an independent conversation',/** 验证生产阶段状态、复核门禁和任务续接在该用例中的具体边界，完成准备、调用和结果断言。用例：one stalled reader can be stopped without delaying an independent conversation。 */ async()=>{
  const reader=createStageReviewReader({/** 提供用例控制的服务响应，覆盖正常返回、超时和取消等读取边界。 */ service:async(_route,args)=>args.session==='blocked'
    ? new Promise(()=>{}) : {state:'none'}}),controller=new AbortController();
  const reading=reader.latest('/workspace','blocked',0,controller.signal);
  const rejected=assert.rejects(reading,{name:'AbortError'});
  assert.equal(await reader.latest('/workspace','other'),null);
  await nextTick();controller.abort();await rejected;
});

test('unavailable or invalid backend state fails closed but inspection remains available',/** 验证生产阶段状态、复核门禁和任务续接在该用例中的具体边界，完成准备、调用和结果断言。用例：unavailable or invalid backend state fails closed but inspection remains available。 */ async()=>{
  for(const service of [async()=>{throw Error('offline');},async()=>null]){
    const reader=createStageReviewReader({service}),policy=createReviewPolicy({readReview:reader.latest});
    const agent={id:'session-test',session:{header:{cwd:'/workspace'}}};
    const denial=await policy({name:'bash',agent});
    assert.equal(denial.info.code,'BENCHFORGE_REVIEW_STORAGE_UNAVAILABLE');
    assert.equal(await policy({name:'read',agent}),undefined);
  }
});

test('the reader never turns an expired deadline into approval and enforces its timeout',/** 验证生产阶段状态、复核门禁和任务续接在该用例中的具体边界，完成准备、调用和结果断言。用例：the reader never turns an expired deadline into approval and enforces its timeout。 */ async()=>{
  const reader=createStageReviewReader({/** 提供用例控制的服务响应，覆盖正常返回、超时和取消等读取边界。 */ service:async()=>({review_id:1,state:'waiting_user',deadline:0})});
  assert.equal((await reader.latest('/workspace','session',9999999)).state,'waiting_user');
  const blocked=createStageReviewReader({/** 提供用例控制的服务响应，覆盖正常返回、超时和取消等读取边界。 */ service:()=>new Promise(()=>{}),reviewReadTimeoutMs:10});
  const keepAlive=setTimeout(()=>{},1000);
  try{await assert.rejects(blocked.latest('/workspace','session'),{name:'TimeoutError'});}
  finally{clearTimeout(keepAlive);}
});
