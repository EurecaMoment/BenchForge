import assert from 'node:assert/strict';
import test from 'node:test';
import {compactCaptureObservation} from './capture_watch.mjs';

test('a compact observation preserves failed checks without turning physical advisories into failures', () => {
  const review = {native_relation_checks: [
    {index:0, passed:false, advisory:true},
    {index:1, passed:false, reason:'measured separation'},
  ]};
  const observation = compactCaptureObservation({run_id:'sf_test', tasks:[
    {id:'sf_test.scene0', result:{scene_review:review}},
  ]});
  assert.deepEqual(observation.tasks[0].diagnostics, [{
    source:'result.scene_review.native_relation_checks[1]', index:1, passed:false, reason:'measured separation',
  }]);
});
