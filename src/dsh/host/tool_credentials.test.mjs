import assert from 'node:assert/strict';
import test from 'node:test';
import {providerEnvironment} from './tool_credentials.mjs';

test('provider credentials are scoped and preserve explicit tool configuration', () => {
  const snapshot = new Map([['ISAAC_TOKEN', {value:'isaac'}], ['MODEL_TOKEN', {value:'model'}]]);
  const base = {PYTHONPATH:'src'};
  assert.deepEqual(providerEnvironment(base, snapshot, 'ISAAC_TOKEN'), {...base, ISAAC_TOKEN:'isaac'});
  assert.deepEqual(base, {PYTHONPATH:'src'});
  assert.deepEqual(providerEnvironment(base, snapshot), base);
  assert.equal(providerEnvironment({...base, ISAAC_TOKEN:'configured'}, snapshot, 'ISAAC_TOKEN').ISAAC_TOKEN, 'configured');
  assert.throws(() => providerEnvironment(base, snapshot, 'invalid=token'), /Invalid/);
});
