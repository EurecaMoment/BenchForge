import {DatabaseSync} from 'node:sqlite';
import path from 'node:path';
import {existsSync} from 'node:fs';

/** Missing storage means no production review, never create it from a reader. */
export function readLatestStageReview(workspace, sessionId, now = Date.now()) {
  const file = path.join(workspace, 'stage-reviews.sqlite');
  if (!existsSync(file)) return null;
  const db = new DatabaseSync(file, {readOnly: true});
  try {
    const row = db.prepare('SELECT id,session,key,stage,artifact,deadline,state,instruction FROM reviews WHERE session=? ORDER BY id DESC LIMIT 1').get(sessionId);
    return row ? {...row, state: row.state === 'waiting_user' && row.deadline * 1000 <= now ? 'elapsed' : row.state} : null;
  } finally {db.close();}
}

/** Read the domain's persisted window; UI and model waits use the same deadline. */
export function readStageReview(workspace,sessionId,reviewId,now=Date.now()) {
  const db=new DatabaseSync(path.join(workspace,'stage-reviews.sqlite'),{readOnly:true});
  try {
    const row=db.prepare('SELECT id,session,key,stage,artifact,deadline,state,instruction FROM reviews WHERE session=? AND id=?').get(sessionId,reviewId);
    if(!row) throw new Error('Stage review does not belong to this conversation');
    const latest=db.prepare('SELECT id FROM reviews WHERE session=? ORDER BY id DESC LIMIT 1').get(sessionId);
    if(latest.id!==row.id) return {...row,state:'superseded'};
    return {...row,state:row.state==='waiting_user'&&row.deadline*1000<=now?'elapsed':row.state};
  } finally {db.close();}
}
