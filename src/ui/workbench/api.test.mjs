import assert from 'node:assert/strict';
import test from 'node:test';
import {createWorkbenchRequest} from './api.js';

test('workbench queries use typed Remote methods; mutations retain the write contract', /** 验证工作台数据读取、生产配置和阶段展示在该用例中的具体边界，完成准备、调用和结果断言。用例：workbench queries use typed Remote methods; mutations retain the write contract。 */ async () => {
  const calls = [], signal = new AbortController().signal;
  const remote = {benchforgeWorkbench: Object.fromEntries(
    ['stageReview', 'progress', 'sceneArtifacts', 'write'].map(method => [method, async (...args) => {
      calls.push({method, args}); return {ok: true, value: {state: 'waiting_user'}};
    }]))};
  const request = createWorkbenchRequest(remote);
  for (const view of ['stage_review', 'progress', 'stage_artifacts']) {
    const result = await request({view, session_id: 'session-owner', workspace_path: '/workspace'}, {signal});
    assert.equal(result.state, 'waiting_user');
  }
  await request({view: 'stage_review', session_id: 'session-owner'}, {body: {action: 'pause'}, signal});
  assert.deepEqual(calls.map(row => row.method), ['stageReview', 'progress', 'sceneArtifacts', 'write']);
  for (const row of calls.slice(0, 3)) {
    assert.equal(row.args[0].session_id, 'session-owner');
    assert.equal(row.args[0].view, undefined);
    assert.equal(row.args[1], signal);
  }
  assert.equal(calls[3].args[1].action, 'pause');
});
