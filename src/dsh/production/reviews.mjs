import {DatabaseSync} from 'node:sqlite';
import path from 'node:path';
import {existsSync} from 'node:fs';

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
export function readLatestStageReview(workspace, sessionId, now = Date.now()) {
  const file = path.join(workspace, 'stage-reviews.sqlite');
  if (!existsSync(file)) return null;
  const db = new DatabaseSync(file, {readOnly: true});
  try {
    const row = db.prepare('SELECT * FROM reviews WHERE session=? ORDER BY id DESC LIMIT 1').get(sessionId);
    return present(row, now);
  } finally {db.close();}
}

/** Read the domain's persisted window; UI and model waits use the same deadline. */
export function readStageReview(workspace,sessionId,reviewId,now=Date.now()) {
  const db=new DatabaseSync(path.join(workspace,'stage-reviews.sqlite'),{readOnly:true});
  try {
    const row=db.prepare('SELECT * FROM reviews WHERE session=? AND id=?').get(sessionId,reviewId);
    if(!row) throw new Error('Stage review does not belong to this conversation');
    const latest=db.prepare('SELECT id FROM reviews WHERE session=? ORDER BY id DESC LIMIT 1').get(sessionId);
    if(latest.id!==row.id) return {...row,state:'superseded'};
    return present(row, now);
  } finally {db.close();}
}
