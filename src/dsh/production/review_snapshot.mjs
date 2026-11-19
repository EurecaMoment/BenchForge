import {z} from 'zod';

const reviewStates = ['waiting_user','paused','needs_input','check_failed','elapsed','continued','superseded'];
export const ReviewSnapshot = z.object({
  review_id:z.number().int().positive(), stage:z.string(), artifact:z.string(),
  deadline:z.number(), state:z.enum(reviewStates), revision:z.number().int().nonnegative().optional(),
});
const pending = new Set(['waiting_user','paused','needs_input','check_failed']);
const released = new Set(['elapsed','continued']);
const gateStates = new Set(['planned','running','incomplete',...pending]);

/** Only domain receipts supply state; a local clock never releases a review. */
/** 合并并校验阶段复核快照，只保留已知状态及有效标识和版本字段。 */
export function reviewSnapshot(receipt, previous) {
  if (!receipt || ![...pending,...released,'superseded'].includes(receipt.state)) return null;
  const parsed = ReviewSnapshot.safeParse({...previous,...receipt,
    review_id:receipt.review_id ?? previous?.review_id,
    artifact:receipt.artifact || previous?.artifact, revision:receipt.revision});
  return parsed.success ? parsed.data : null;
}

/** 将有效复核快照包装为通知来源，并保存前一次复核标识。 */
export function reviewNotice(receipt, previous) {
  const review = reviewSnapshot(receipt, previous);
  return review ? {review, previousReviewId:previous?.review_id} : {};
}

/** 判断通知是否仍对应当前复核，拒绝旧复核或较低版本覆盖新状态。 */
export function currentReviewSnapshot(current, review, {previousReviewId, allowNewReview=false}={}) {
  if (!current) return allowNewReview;
  if (current.review_id !== review.review_id)
    return current.review_id === previousReviewId ||
      allowNewReview && review.review_id > current.review_id && review.state !== 'superseded';
  return current.revision === undefined ||
    review.revision !== undefined && review.revision >= current.revision;
}

/** 仅把仍然有效的复核通知合入当前视图，并同步门禁相关的生产状态。 */
export function reviewChanges(view, source) {
  const current = view.review, parsed = ReviewSnapshot.safeParse(source?.review);
  if (!current || !parsed.success) return {};
  const review = parsed.data;
  if (!currentReviewSnapshot(current,review,source)) return {};
  const state = gateStates.has(view.state) && (pending.has(review.state) || released.has(review.state))
    ? released.has(review.state) ? 'running' : review.state : view.state;
  return {review, state};
}
