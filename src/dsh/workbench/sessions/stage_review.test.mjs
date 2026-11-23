import assert from 'node:assert/strict';
import test from 'node:test';
import {currentAccessScope} from '../../access/context.mjs';
import {createWorkbenchReviewReader} from './stage_review.mjs';

test('first workbench read binds each conversation to its own review in a shared project', async () => {
  const workspace = '/shared-project';
  const states = new Map([['first', {state:'waiting_user', review_id:1}]]);
  const registered = new Set();
  const disposed = [];
  const query = {observeSession: async id => ({
    header: {id, cwd:workspace}, [Symbol.dispose]() {disposed.push(id);},
  })};
  const access = {scopeForHeader: async header => ({session_id:header.id, root_path:header.cwd})};
  const reviews = {latest: async (root, id) => {
    assert.deepEqual(currentAccessScope(), {session_id:id, root_path:root});
    registered.add(id);
    return states.get(id) ?? null;
  }};
  const read = createWorkbenchReviewReader(query, access, reviews);
  assert.deepEqual(await read(workspace, 'second'), {state:'none', can_continue:false});
  assert.deepEqual(await read(workspace, 'first'), states.get('first'));
  await assert.rejects(read('/unrelated-project', 'first'), {status:403});
  assert.deepEqual([...registered], ['second','first']);
  assert.deepEqual(disposed, ['second','first','first']);
  const wrongSession = createWorkbenchReviewReader({observeSession: () => query.observeSession('first')}, access, reviews);
  await assert.rejects(wrongSession(workspace, 'second'), {status:403});
});
