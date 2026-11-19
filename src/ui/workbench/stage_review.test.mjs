import assert from 'node:assert/strict';
import test from 'node:test';
import {createProductComponents} from './components/products.js';

/** 将组件渲染调用转换为可断言的普通节点结构。 */
const h=(type,props,...children)=>({type,props,children});
const React={createElement:h,/** 提供确定性时间状态和空更新器，避免组件测试依赖真实 React 渲染器。 */ useState:()=>[Date.now(),()=>{}],/** 在静态渲染测试中忽略生命周期副作用。 */ useEffect:()=>{},/** 提供可写引用容器，供静态组件渲染保存接收时间。 */ useRef:()=>({current:{}})};
const {StageReviewPanel}=createProductComponents({React,components:{},helpers:{
  STAGES:[{id:'S1',key:'plan'}],/** 将复核操作按钮转换为可检索的测试节点。 */ button:(label,click,props)=>h('button',props,label),
}});
/** 用最小 React 适配器渲染复核面板，并序列化结果供状态文案断言。 */
const render=value=>JSON.stringify(StageReviewPanel({value,/** 保留本地化键，便于测试断言组件选择了正确文案。 */ t:key=>key,/** 提供静态渲染所需的复核操作占位函数。 */ act:()=>{},/** 提供静态渲染所需的修改需求占位函数。 */ change:()=>{}}));
const value={review_id:1,stage:'S1',state:'waiting_user',server_time:100,deadline:160,
  policy_version:1,checks_completed:2,waits_completed:1,round_number:2,rounds_required:3};

test('review panel displays backend check/wait counts without inferring release',/** 验证工作台数据读取、生产配置和阶段展示在该用例中的具体边界，完成准备、调用和结果断言。用例：review panel displays backend check/wait counts without inferring release。 */ ()=>{
  const view=render(value);
  assert.match(view,/reviewChecks.*2\/3.*reviewWaits.*1\/3/);
  const expired=render({...value,deadline:100});
  assert.match(expired,/reviewChecking/);assert.doesNotMatch(expired,/reviewFinished/);
});

test('failed checks expose a repair action and do not offer early continuation',/** 验证工作台数据读取、生产配置和阶段展示在该用例中的具体边界，完成准备、调用和结果断言。用例：failed checks expose a repair action and do not offer early continuation。 */ ()=>{
  const view=render({...value,state:'check_failed',check_failure:{error:'Source changed'}});
  assert.match(view,/Source changed/);assert.match(view,/data-review-retry/);
  assert.doesNotMatch(view,/data-review-continue/);
});
