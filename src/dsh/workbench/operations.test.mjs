import test from 'node:test';
import assert from 'node:assert/strict';
import {workspaceOperations} from './operations.mjs';

test('workspace reads use production configuration and preserve conversation scope', async () => {
  let invocation;
  const rows = await workspaceOperations({productionPython: 'python', productionConfig: '/instance/service.json',
    _executePython: async (python, args) => {
      invocation = {python, args};
      return {stdout: JSON.stringify({operations: [{id: 'operation-a'}]})};
    }}, {path: '/workspace'}, {session_id: 'session-a', session_ids: ['session-a', 'session-b']},
    {limit: 30, tools: ['plan']});
  assert.equal(invocation.args[invocation.args.indexOf('--config') + 1], '/instance/service.json');
  assert.deepEqual(JSON.parse(invocation.args[invocation.args.indexOf('--sessions') + 1]), ['session-a', 'session-b']);
  assert.equal(invocation.args.includes('--history'), false);
  assert.deepEqual(rows, [{id: 'operation-a'}]);
});
