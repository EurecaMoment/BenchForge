import assert from 'node:assert/strict';
import test from 'node:test';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {setImmediate as nextTick} from 'node:timers/promises';
import {DatabaseSync} from 'node:sqlite';
import {createStageReviewReader} from './reviews.mjs';
import {createReviewPolicy} from './review_policy.mjs';

async function ledger(t) {
  const root=await fs.mkdtemp(path.join(os.tmpdir(),'benchforge-review-lock-'));
  const db=new DatabaseSync(path.join(root,'stage-reviews.sqlite'));
  t.after(async()=>{db.close();await fs.rm(root,{recursive:true,force:true});});
  db.exec('CREATE TABLE reviews(id INTEGER PRIMARY KEY,session TEXT,state TEXT,deadline REAL,progress TEXT)');
  db.prepare('INSERT INTO reviews VALUES(?,?,?,?,?)').run(1,'session-test','continued',0,'{}');
  return {root,db,reader:createStageReviewReader({reviewReadTimeoutMs:1000,reviewRetryIntervalMs:5})};
}

test('a real exclusive writer no longer aborts review reads or blocks the event loop',async t=>{
  const {root,db,reader}=await ledger(t);
  db.exec("BEGIN EXCLUSIVE; UPDATE reviews SET state='paused'");
  let settled=false;
  const reading=reader.byId(root,'session-test',1).then(value=>{settled=true;return value;});
  await nextTick();assert.equal(settled,false);
  db.exec('COMMIT');
  assert.equal((await reading).state,'paused');
  assert.equal((await reader.latest(root,'session-test')).state,'paused');
});

test('cancelling one locked reader leaves another reader and the writer intact',async t=>{
  const {root,db,reader}=await ledger(t),controller=new AbortController();
  db.exec('BEGIN EXCLUSIVE');
  const canceled=reader.latest(root,'session-test',Date.now(),controller.signal);
  const rejected=assert.rejects(canceled,{name:'AbortError'});
  const independent=reader.latest(root,'session-test');
  controller.abort(new DOMException('Stopped','AbortError'));
  await rejected;
  db.exec('COMMIT');
  assert.equal((await independent).state,'continued');
});

test('persistent contention fails closed with a storage error instead of requesting user approval',async t=>{
  const {root,db}=await ledger(t);
  const reader=createStageReviewReader({reviewReadTimeoutMs:15,reviewRetryIntervalMs:2});
  db.exec('BEGIN EXCLUSIVE');
  await assert.rejects(reader.byId(root,'session-test',1),{code:'BENCHFORGE_REVIEW_STORAGE_BUSY'});
  const guard=createReviewPolicy({readReview:reader.latest});
  const agent={id:'session-test',session:{header:{cwd:root}}};
  const denial=await guard({name:'bash',agent});
  assert.equal(denial.info.code,'BENCHFORGE_REVIEW_STORAGE_BUSY');
  assert.doesNotMatch(denial.reason,/用户指令|waiting_user/);
  assert.equal(await guard({name:'read',agent}),undefined);
  db.exec('COMMIT');
});

test('the reader retains supersession and reports damaged storage without lock retries',async t=>{
  const {root,db,reader}=await ledger(t);
  db.prepare('INSERT INTO reviews VALUES(?,?,?,?,?)').run(2,'session-test','waiting_user',0,'{}');
  assert.equal((await reader.byId(root,'session-test',1)).state,'superseded');
  await assert.rejects(reader.byId(root,'unrelated',1),/does not belong/);
  db.exec('DROP TABLE reviews');
  await assert.rejects(reader.latest(root,'session-test'),/no such table/);
});
