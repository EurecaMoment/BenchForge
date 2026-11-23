export const captureBudgetCode = 'S2_CAPTURE_BUDGET_EXHAUSTED';
export const captureProducers = new Set(['benchforge_capture','benchforge_refine','benchforge_run','benchforge_extend','benchforge_retry']);

export function budgetRejection(value) {
  let receipt = value;
  if (typeof value === 'string') {
    try { receipt = JSON.parse(value.trim().replace(/^Error:\s*/, '')); }
    catch { return; }
  }
  if (receipt?.code !== captureBudgetCode) return;
  const issue = Array.isArray(receipt.issues) && receipt.issues.find(row => row?.code === captureBudgetCode);
  const budget = issue?.capture_budget ?? receipt.capture_budget;
  if (budget?.remaining === 0 && budget.scene_id) return budget;
}

export function budgetMatches(budget, run, tasks = []) {
  if (!budget || !run || tasks.length > 1) return false;
  const sceneKey = tasks[0]?.capture_budget?.scene_key || tasks[0]?.capture_scene_key;
  if (sceneKey && budget.scene_key) return sceneKey === budget.scene_key;
  return (budget.evidence || []).some(row => row.task_id?.split('.')[0] === run);
}
