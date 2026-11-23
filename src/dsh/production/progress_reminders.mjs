/** 按业务事实和可见回复决定是否提醒，工具计数、压缩和读报告不构成新的生产进展。 */
export function progressReminder({view, review, previous, now, silenceMs, repeatMs}) {
  const fingerprint=JSON.stringify([view.turn,view.stage,view.state,view.plan,view.run,view.completed,
    view.error,review?.review_id,review?.state,review?.revision,review?.waits_completed]);
  const changed=previous ? previous.fingerprint!==fingerprint : Boolean(review);
  const silent=now-view.lastTextAt>=silenceMs;
  if(!changed && (!silent || previous &&
      (previous.textSeq===view.lastTextSeq || now-previous.at<repeatMs)))return null;
  return {fingerprint,textSeq:view.lastTextSeq,at:now};
}
