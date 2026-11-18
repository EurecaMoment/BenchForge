/** Production uses the Host's permission presets; the UI never grants tool access. */
export function canStartProduction(selection) {
  return ['workspace-write', 'danger-full-access'].includes(selection?.currentValue);
}
