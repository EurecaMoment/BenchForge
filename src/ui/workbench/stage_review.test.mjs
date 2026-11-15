import assert from 'node:assert/strict';
import test from 'node:test';
import {createProductComponents} from './components/products.js';

const h=(type,props,...children)=>({type,props,children});
const React={createElement:h,useState:()=>[Date.now(),()=>{}],useEffect:()=>{},useRef:()=>({current:{}})};
const {StageReviewPanel}=createProductComponents({React,components:{},helpers:{
  STAGES:[{id:'S1',key:'plan'}],button:(label,click,props)=>h('button',props,label),
}});
const render=value=>JSON.stringify(StageReviewPanel({value,t:key=>key,act:()=>{},change:()=>{}}));
const value={review_id:1,stage:'S1',state:'waiting_user',server_time:100,deadline:160,
  policy_version:1,checks_completed:2,waits_completed:1,round_number:2,rounds_required:3};

test('review panel displays backend check/wait counts without inferring release',()=>{
  const view=render(value);
  assert.match(view,/reviewChecks.*2\/3.*reviewWaits.*1\/3/);
  const expired=render({...value,deadline:100});
  assert.match(expired,/reviewChecking/);assert.doesNotMatch(expired,/reviewFinished/);
});

test('failed checks expose a repair action and do not offer early continuation',()=>{
  const view=render({...value,state:'check_failed',check_failure:{error:'Source changed'}});
  assert.match(view,/Source changed/);assert.match(view,/data-review-retry/);
  assert.doesNotMatch(view,/data-review-continue/);
});
