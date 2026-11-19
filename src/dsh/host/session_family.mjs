// DSH owns the parent catalog and immutable session headers. Browser-supplied
// session arrays never participate in this scope.
/** 读取父会话的原生子 Agent 目录，并在提取后立即释放观察快照。 */
export async function readChildCatalog(query, parentId, signal) {
  const observation = await query.observeSession(parentId, {signal});
  try {
    const catalog = observation.projections?.values.subagentCatalog;
    if (!Array.isArray(catalog)) throw new Error('The parent subagent catalog is unavailable');
    return catalog;
  } finally {
    observation[Symbol.dispose]();
  }
}

/** 创建有界的子会话读取器，优先观察活动或已验证会话并缓存其父子身份。 */
export function sessionFamilyReader(ctx) {
  const verified = new Map();
  return /** 按父会话的原生子 Agent 目录选择有界观察集合，核验父子身份后返回活动摘要。 */ async (parentId, signal) => {
    const query = ctx.get('sessionQuery');
    if (!query) return [];
    let entries;
    try { entries = await readChildCatalog(query, parentId, signal); }
    catch (error) { if (signal?.aborted) throw error; return []; }
    const sessions = ctx.get('sessions');
    // Rank the existing catalog and resident handles; never open historical
    // sessions merely to decide which eight observations to request.
    const candidates = entries.map((entry, index) => {
      const resident = sessions?.get(entry.id), cached = verified.get(entry.id);
      const created=typeof entry.createdAt==='number' ? entry.createdAt : Date.parse(entry.createdAt);
      return {entry, resident, index, created: Number.isFinite(created) ? created : 0,
        active: Boolean(resident && cached?.header?.parentSession === parentId && cached.state === 'running')};
    }).sort((a, b) => Number(b.active) - Number(a.active) || Number(Boolean(b.resident)) - Number(Boolean(a.resident)) ||
      b.created - a.created || b.index - a.index).slice(0, 8);
    return (await Promise.all(candidates.map(/** 观察候选子会话的真实状态并验证父会话归属，读取后释放快照。 */ async ({entry, resident}) => {
      signal?.throwIfAborted();
      let header = resident?.header || verified.get(entry.id)?.header;
      let state = resident ? 'unknown' : 'inactive';
      if (query && (resident || !header)) {
        let observation;
        try {
          observation = await query.observeSession(entry.id, resident ? {signal} : {signal, projectionMode: 'none'});
          header = observation.header;
          if (resident) {
            const timing = observation.projections?.values.subagentTiming;
            state = timing?.active ? 'running' : timing ? 'inactive' : 'unknown';
          }
          verified.set(entry.id, {header, state});
          if (verified.size > 128) verified.delete(verified.keys().next().value);
        } catch (error) {
          if (signal?.aborted) throw error;
          return null;
        } finally { observation?.[Symbol.dispose]?.(); }
      }
      if (header?.parentSession !== parentId) return null;
      return {...entry, state};
    }))).filter(Boolean);
  };
}
