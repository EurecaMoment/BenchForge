import assert from 'node:assert/strict';
import test from 'node:test';
import {progressReminder} from './progress_reminders.mjs';

test('unchanged silent work receives one reminder, even after more tools and compaction', () => {
  const view={turn:1,stage:'S2',state:'needs_repair',lastTextAt:1000,lastTextSeq:10};
  const args={view,now:61000,silenceMs:60000,repeatMs:300000};
  const previous=progressReminder(args);
  assert.ok(previous);
  assert.equal(progressReminder({...args,view:{...view,tools:999,revision:5000},previous,now:900000}),null);
  assert.equal(progressReminder({...args,view:{...view,lastTextAt:100000,lastTextSeq:20},previous,now:161000}),null);
  assert.ok(progressReminder({...args,view:{...view,lastTextAt:100000,lastTextSeq:20},previous,now:400000}));
  assert.ok(progressReminder({...args,view:{...view,state:'blocked'},previous,now:62000}));
});
