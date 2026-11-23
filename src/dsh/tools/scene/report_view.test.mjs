import assert from 'node:assert/strict';
import test from 'node:test';
import {projectReport,splitReportRequest} from './report_view.mjs';

test('large reports expose pointers and exact pages without modifying the evidence',()=>{
  const original={passed:false,physical_issues:Array.from({length:200},(_,i)=>({object_id:'obj'+i,reason:'gap',detail:'x'.repeat(500)}))};
  const before=JSON.stringify(original);
  const result=projectReport(original);
  assert.equal(result.report_view.complete,false);
  assert.equal(result.data.passed,false);
  assert.equal(result.data.physical_issues.json_pointer,'/physical_issues');
  assert.equal(result.data.physical_issues.total,200);
  const page=projectReport(original,{json_pointer:'/physical_issues',offset:20,limit:10});
  assert.deepEqual(page.data,original.physical_issues.slice(20,30));
  assert.equal(page.report_view.next_offset,30);
  assert.equal(projectReport(original,{detail:true}),original);
  assert.equal(JSON.stringify(original),before);
});

test('selected pages never skip omitted items and pointers use original array positions',()=>{
  const report={issues:Array.from({length:50},(_,i)=>({id:i,detail:'x'.repeat(2000)}))};
  const result=projectReport(report,{json_pointer:'/issues',offset:10,limit:20},4000);
  assert.ok(result.data.length<20);
  assert.equal(result.report_view.next_offset,10+result.data.length);
  assert.equal(result.data[0].detail.json_pointer,'/issues/10/detail');
  assert.equal(projectReport({'a/b':{'~x':false}},{json_pointer:'/a~1b/~0x'}).data,false);
  assert.throws(()=>projectReport({}, {json_pointer:'/__proto__'}),/does not exist/);
  assert.throws(()=>splitReportRequest({limit:0}),/between/);
  assert.throws(()=>splitReportRequest({offset:-1}),/nonnegative/);
});

test('presentation arguments stay outside strict service requests and small results stay unchanged',()=>{
  const request={task_id:'sf_one',file:'scene_review.json',revision:0};
  assert.deepEqual(splitReportRequest({...request,json_pointer:'/issues',limit:5}).request,request);
  const value={passed:false,issues:['actual failure']};
  assert.equal(projectReport(value),value);
});
