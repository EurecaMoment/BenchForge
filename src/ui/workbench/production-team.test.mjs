import assert from 'node:assert/strict';
import test from 'node:test';
import {enableProductionTeam} from './production-team.js';

/** 提供原生会话投影及团队接口替身，验证开关只作用于选中会话。 */
function fixture(enabled = false) {
  const calls = [];
  const ctx = {
    sessions: {binding(sessionId) {
      assert.equal(sessionId, 'selected-session');
      return {session: {projections: {faceOf(name) {
        assert.equal(name, 'agentTeam');
        return {getSnapshot: () => ({enabled})};
      }}}};
    }},
    remote: {agentTeams: {async setEnabled(...args) {calls.push(args); return {ok: true};}}},
  };
  return {ctx, calls};
}

test('production activation enables only the selected native team', async () => {
  const {ctx, calls} = fixture();
  await enableProductionTeam(ctx, 'selected-session', 'retry');
  assert.deepEqual(calls, [['selected-session', true]]);
});

test('already enabled team is left unchanged', async () => {
  const {ctx, calls} = fixture(true);
  await enableProductionTeam(ctx, 'selected-session', 'retry');
  assert.deepEqual(calls, []);
});

test('native rejection and missing service remain visible and retryable', async () => {
  const {ctx} = fixture();
  ctx.remote.agentTeams.setEnabled = async () => ({ok: false, error: {message: 'session unavailable'}});
  await assert.rejects(enableProductionTeam(ctx, 'selected-session', 'retry'), /session unavailable/);
  delete ctx.remote.agentTeams;
  await assert.rejects(enableProductionTeam(ctx, 'selected-session', 'retry'), /retry/);
});
