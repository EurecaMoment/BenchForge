import {DatabaseSync} from 'node:sqlite';
import path from 'node:path';
import {existsSync} from 'node:fs';
import {setTimeout as sleep} from 'node:timers/promises';
import {HarnessError} from '@deepseek-ai/dsh-error';
import Schema from '@deepseek-ai/schemastery';

export const reviewReadConfig = {
  reviewReadTimeoutMs: Schema.number().min(1).default(10000),
  reviewRetryIntervalMs: Schema.number().min(1).default(25),
};

export class StageReviewReadError extends HarnessError {
  constructor(file, cause) {
    super(`Stage review storage is busy: ${file}. Approval could not be verified; dependent production remains stopped. Retry after the writer finishes.`,
      'BENCHFORGE_REVIEW_STORAGE_BUSY', {cause});
  }
}

const busy = error => error?.code==='ERR_SQLITE_ERROR' && [5,6].includes(error.errcode & 255);

function present(row, now) {
  if (!row) return null;
  const progress = JSON.parse(row.progress || '{}'), rounds = progress.rounds || [];
  // Only the backend records release. A deadline alone proves neither a
  // self-check nor three waits, including before a legacy row is migrated.
  return {...row, state: progress.pending_check && ['elapsed','continued'].includes(row.state) ? 'waiting_user' : row.state,
    checks_completed: rounds.length, waits_completed: rounds.filter(item => item.wait_completed_at !== undefined).length,
    round_number: Math.min(3, rounds.length || 1), rounds_required: 3,
    remaining_seconds: Math.max(0, Math.ceil(row.deadline - now / 1000)), check_failure: progress.failure};
}

/** Missing storage means no production review, never create it from a reader. */
function snapshot(workspace, sessionId, reviewId, now) {
  const file = path.join(workspace, 'stage-reviews.sqlite');
  if (reviewId===undefined && !existsSync(file)) return null;
  const db = new DatabaseSync(file, {readOnly: true});
  try {
    // The selected review and its successor must come from the same committed snapshot.
    // Never wait inside SQLite: the event loop must remain able to process Stop.
    db.exec('PRAGMA busy_timeout=0; BEGIN');
    const latest = db.prepare('SELECT * FROM reviews WHERE session=? ORDER BY id DESC LIMIT 1').get(sessionId);
    if (reviewId===undefined) return present(latest, now);
    const row=db.prepare('SELECT * FROM reviews WHERE session=? AND id=?').get(sessionId,reviewId);
    if(!row) throw new Error('Stage review does not belong to this conversation');
    if(latest.id!==row.id) return {...row,state:'superseded'};
    return present(row,now);
  } finally {db.close();}
}

/** Retry only lock contention; never cache approval, replay a write, or hide corruption. */
export function createStageReviewReader({reviewReadTimeoutMs=10000,reviewRetryIntervalMs=25}={}) {
  async function read(workspace,sessionId,reviewId,now,signal) {
    const deadline=performance.now()+reviewReadTimeoutMs;
    for (;;) {
      signal?.throwIfAborted();
      try {return snapshot(workspace,sessionId,reviewId,now);}
      catch(error) {
        if(!busy(error))throw error;
        const remaining=deadline-performance.now();
        if(remaining<=0)throw new StageReviewReadError(path.join(workspace,'stage-reviews.sqlite'),error);
        await sleep(Math.min(reviewRetryIntervalMs,remaining),undefined,{signal});
      }
    }
  }
  return {
    latest:(workspace,sessionId,now=Date.now(),signal)=>read(workspace,sessionId,undefined,now,signal),
    byId:(workspace,sessionId,reviewId,now=Date.now(),signal)=>read(workspace,sessionId,reviewId,now,signal),
  };
}

export const {latest:readLatestStageReview,byId:readStageReview}=createStageReviewReader();
