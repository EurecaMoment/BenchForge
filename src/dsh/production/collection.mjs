/** A public count summary from an executed collection validator, never filenames. */
/** 从已完成或待补证据的采集、标注回执中提取交付数量摘要，过滤不符合约定的值。 */
export function collectionSummary(receipt) {
  if (!['benchforge.collection-stage/v1', 'benchforge.annotation-stage/v1'].includes(receipt?.schema) ||
      !['completed', 'needs_evidence'].includes(receipt.state)) return null;
  /** 只接受非负安全整数作为回执计数，其他值保留为未知。 */
  const number = value => Number.isSafeInteger(value) && value >= 0 ? value : null;
  const counts = receipt.collection || {};
  return {state: receipt.state, records: number(counts.records ?? receipt.items),
    required: number(receipt.required_items), scenes: number(counts.scenes),
    media: number(counts.media_files), episodes: number(counts.native_episodes),
    samples: number(counts.native_samples), annotations: number(receipt.annotations?.items ?? receipt.items)};
}
