import {test} from 'node:test';
import assert from 'node:assert/strict';
import PostgresSessionQuery from './session_query.mjs';

function provider() {
  const value = Object.create(PostgresSessionQuery.prototype);
  Object.assign(value, {lifetime: new AbortController(), queue: Promise.resolve(), hostId: 'a'.repeat(64), generation: 'v1'});
  value.filterSessions = async () => [{header: {id: 'session-a'}, sources: ['live']}];
  value.synchronize = async () => value.generation;
  value.client = async () => async (_route, args) => {
    value.last = args;
    return {items: [{sessionId: 'session-a', seq: args.offset}], hasMore: args.offset === 0};
  };
  return value;
}

test('search pagination is bound to request and indexed generation', async () => {
  const query = provider(), request = {sessionId: 'session-a', query: '场景', limit: 1};
  const first = await query.searchEvents(request);
  assert.equal(first.session.id, 'session-a');
  assert.equal((await query.searchEvents({...request, cursor: first.nextCursor})).items[0].seq, 1);
  query.generation = 'v2';
  await assert.rejects(query.searchEvents({...request, cursor: first.nextCursor}), {code: 'SESSION_QUERY_STALE_CURSOR'});
  await assert.rejects(query.searchEvents({...request, query: '其它', cursor: first.nextCursor}), {code: 'SESSION_QUERY_INVALID_CURSOR'});
});

test('search validates limits, query and official inclusive filters', async () => {
  const query = provider();
  await assert.rejects(query.searchSessions({query: 'x', limit: 0}), {code: 'SESSION_QUERY_INVALID_LIMIT'});
  await assert.rejects(query.searchSessions({query: 3}), {code: 'SESSION_QUERY_INVALID_QUERY'});
  const page = await query.searchSessions({query: '场景', eventFilters: [{kind: 'seq', from: 1, to: 4}]});
  assert.deepEqual(query.last.filters, [{kind: 'seq', from: 1, to: 4}]);
  assert.equal(page.items[0].bestMatch.sessionId, 'session-a');
});

test('queued search cancels immediately without waiting for previous search', async () => {
  const query = provider(), pending = Promise.withResolvers(), abort = new AbortController();
  query.queue = pending.promise;
  const call = query.searchSessions({query: '场景'}, {signal: abort.signal});
  abort.abort();
  await assert.rejects(call, {code: 'SESSION_QUERY_ABORTED'});
  pending.resolve();
  await query.queue;
  assert.equal(query.last, undefined);
});
