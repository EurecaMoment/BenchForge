import {ApiError} from '../../host/session_scope.mjs';
import {runWithAccessScope} from '../../access/context.mjs';

/** 在原生会话绑定内读取阶段快照，包括尚未发送第一条消息的会话。 */
export function createWorkbenchReviewReader(query, access, reviews) {
  return async (workspace, sessionId, signal) => {
    const observation = await query.observeSession(sessionId, {signal, projectionMode: 'none'});
    let scope;
    try {
      const {header} = observation;
      if (header.id !== sessionId || header.cwd !== workspace)
        throw new ApiError('Review workspace and conversation do not match', 403);
      scope = await access.scopeForHeader(header);
    } finally {
      observation[Symbol.dispose]();
    }
    const result = await runWithAccessScope(scope, () => reviews.latest(workspace, sessionId, Date.now(), signal));
    return result ?? {state: 'none', can_continue: false};
  };
}
