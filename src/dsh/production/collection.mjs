/** A public count summary from an executed collection validator, never filenames. */
export function collectionSummary(receipt) {
  if (!['benchforge.collection-stage/v1', 'benchforge.annotation-stage/v1'].includes(receipt?.schema) ||
      !['completed', 'needs_evidence'].includes(receipt.state)) return null;
  const number = value => Number.isSafeInteger(value) && value >= 0 ? value : null;
  const counts = receipt.collection || {};
  return {state: receipt.state, records: number(counts.records ?? receipt.items),
    required: number(receipt.required_items), scenes: number(counts.scenes),
    media: number(counts.media_files), episodes: number(counts.native_episodes),
    samples: number(counts.native_samples), annotations: number(receipt.annotations?.items ?? receipt.items)};
}
