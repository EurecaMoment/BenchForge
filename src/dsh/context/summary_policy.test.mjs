import assert from 'node:assert/strict';
import test from 'node:test';
import {apply} from './summary_policy.mjs';

test('checkpoint guidance uses the replayable preset prompt instead of rewriting model requests', () => {
  const sections=[];
  apply({systemPrompt:{section:row=>sections.push(row)}}, {targetTokens:4000});
  assert.equal(sections.length,1);
  const text=sections[0].text();
  assert.match(text,/4000 tokens/);
  assert.match(text,/user hard requirements/);
  assert.match(text,/distinguish observations from hypotheses/);
  assert.match(text,/exceeding the size target/);
  assert.equal(sections[0].text(),text);
});
