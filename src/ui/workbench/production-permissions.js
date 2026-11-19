/** Production uses the Host's permission presets; the UI never grants tool access. */
/** 仅在当前会话具备工作区写入或完整访问权限时允许启动生产。 */
export function canStartProduction(selection) {
  return ['workspace-write', 'danger-full-access'].includes(selection?.currentValue);
}
