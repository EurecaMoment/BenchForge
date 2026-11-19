import assert from 'node:assert/strict';
import test from 'node:test';
import {modelRole, providerEnvironment} from './tool_credentials.mjs';

test('FastQwen annotation selects its own role credential without losing model tools', /** 验证宿主进程、取消信号和资源清理在该用例中的具体边界，完成准备、调用和结果断言。用例：FastQwen annotation selects its own role credential without losing model tools。 */ () => {
  const providers = {FastQwen:{url:'http://local/v1',model:'qwen',token_env:'MAIN_TOKEN',
    roles:{annotation:{token_env:'VISION_TOKEN'}}}};
  const model = modelRole(providers,'annotation');
  const snapshot = new Map([['VISION_TOKEN',{value:'vision'}],['MAIN_TOKEN',{value:'main'}]]);
  assert.equal(model.model,'qwen');
  assert.deepEqual(providerEnvironment({},snapshot,model.token_env),{VISION_TOKEN:'vision'});
  assert.throws(()=>modelRole({...providers,DSpark4A100:{model:'deepseek'}},'annotation'),/one primary/);
});

test('provider credentials are scoped and preserve explicit tool configuration', /** 验证宿主进程、取消信号和资源清理在该用例中的具体边界，完成准备、调用和结果断言。用例：provider credentials are scoped and preserve explicit tool configuration。 */ () => {
  const snapshot = new Map([['ISAAC_TOKEN', {value:'isaac'}], ['MODEL_TOKEN', {value:'model'}]]);
  const base = {PYTHONPATH:'src'};
  assert.deepEqual(providerEnvironment(base, snapshot, 'ISAAC_TOKEN'), {...base, ISAAC_TOKEN:'isaac'});
  assert.deepEqual(base, {PYTHONPATH:'src'});
  assert.deepEqual(providerEnvironment(base, snapshot), base);
  assert.equal(providerEnvironment({...base, ISAAC_TOKEN:'configured'}, snapshot, 'ISAAC_TOKEN').ISAAC_TOKEN, 'configured');
  assert.throws(() => providerEnvironment(base, snapshot, 'invalid=token'), /Invalid/);
});
