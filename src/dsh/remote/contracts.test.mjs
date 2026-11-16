import assert from 'node:assert/strict';
import test from 'node:test';
import {WorkbenchRemote} from './lib/types/index.js';

function service(dispatch, kind = 'owner') {
  const remote = new WorkbenchRemote(dispatch);
  remote.ctx = {invocation: {peer: {principal: {kind}}}};
  return remote;
}

test('typed workbench routes retain session identity, cancellation and backend state', async () => {
  const signal = new AbortController().signal;
  const review = {state: 'check_failed', can_continue: false, wait_seconds: 300,
    checks_completed: 1, check_failure: {error: 'Missing real evidence'}};
  const remote = service(async (url, request) => {
    assert.equal(url.searchParams.get('view'), 'stage_review');
    assert.equal(url.searchParams.get('session_id'), 'session-owner');
    assert.equal(request.signal, signal);
    return review;
  });
  assert.deepEqual(await remote.stageReview({session_id: 'session-owner', workspace_path: '/workspace'}, signal), review);
});

test('typed artifact and progress routes reject missing projections and require the owner', async () => {
  const query = {session_id: 'session-owner', workspace_path: '/workspace'};
  const remote = service(async () => ({state: 'completed'}));
  await assert.rejects(remote.sceneArtifacts({...query, scene: 'sf_one'}, undefined), /Invalid scene artifact/);
  await assert.rejects(remote.progress(query, undefined), /Invalid progress/);
  let called = false;
  const forbidden = service(async () => {called = true; return {};}, 'guest');
  await assert.rejects(forbidden.stageReview(query, undefined), /requires the host owner/);
  assert.equal(called, false);
});
