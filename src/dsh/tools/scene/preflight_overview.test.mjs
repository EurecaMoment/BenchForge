import assert from 'node:assert/strict';
import test from 'node:test';
import {renderPreflight} from './contracts.mjs';
const read=(args,report)=>JSON.parse(renderPreflight(args,report)[0].text);
const warning={severity:'warning',message:'x'.repeat(120)};
const report={passed:false,status:'blocked',issues:[...Array(100).fill(warning),
  {severity:'error',code:'support',path:'/objects/3/support',message:'missing support'},
  {blocking:true,code:'material',path:'/objects/4/material_id',message:'missing material'}]};
test('late blockers remain visible, original pointers and detail stay stable',()=>{
  const out=read({},report);
  assert.equal(out.issues.total,102);
  assert.deepEqual(out.blocking.map(i=>i.code),['support','material']);
  assert.deepEqual(read({json_pointer:out.blocking[0].json_pointer},report).data,report.issues[100]);
  assert.deepEqual(read({detail:true},report).issues,report.issues);
});
test('many blockers preserve total and honest continuation',()=>{
  const large={...report,issues:Array(300).fill({...report.issues[100],message:'x'.repeat(500)})};
  const out=read({},large);
  assert.equal(out.blocking_total,300);
  assert.equal(out.next_blocking_pointer,'/issues/'+out.blocking.length);
  assert.ok(JSON.stringify(out).length<6000);
});
