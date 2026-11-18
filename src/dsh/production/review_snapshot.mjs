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
export function reviewSnapshot(receipt, previous) {
  if (!receipt || ![...pending,...released,'superseded'].includes(receipt.state)) return null;
  const parsed = ReviewSnapshot.safeParse({...previous,...receipt,
    review_id:receipt.review_id ?? previous?.review_id,
    artifact:receipt.artifact || previous?.artifact, revision:receipt.revision});
  return parsed.success ? parsed.data : null;
}

export function reviewNotice(receipt, previous) {
  const review = reviewSnapshot(receipt, previous);
  return review ? {review, previousReviewId:previous?.review_id} : {};
}

export function currentReviewSnapshot(current, review, {previousReviewId, allowNewReview=false}={}) {
  if (!current) return allowNewReview;
  if (current.review_id !== review.review_id)
    return current.review_id === previousReviewId ||
      allowNewReview && review.review_id > current.review_id && review.state !== 'superseded';
  return current.revision === undefined ||
    review.revision !== undefined && review.revision >= current.revision;
}

export function reviewChanges(view, source) {
  const current = view.review, parsed = ReviewSnapshot.safeParse(source?.review);
  if (!current || !parsed.success) return {};
  const review = parsed.data;
  if (!currentReviewSnapshot(current,review,source)) return {};
  const state = gateStates.has(view.state) && (pending.has(review.state) || released.has(review.state))
    ? released.has(review.state) ? 'running' : review.state : view.state;
  return {review, state};
}
