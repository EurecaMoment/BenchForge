/** 通过原生团队接口启用当前对话协作；已有设置不重复写入，不改变全局默认。 */
export async function enableProductionTeam(ctx, sessionId, failureMessage) {
  const team = ctx.sessions.binding(sessionId).session.projections.faceOf('agentTeam').getSnapshot();
  if (team?.enabled) return;
  if (!ctx.remote.agentTeams) throw new Error(failureMessage);
  const result = await ctx.remote.agentTeams.setEnabled(sessionId, true);
  if (!result.ok) throw new Error(result.error?.message || failureMessage);
}
