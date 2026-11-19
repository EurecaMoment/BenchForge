import {executePython} from '../host/python_process.mjs';

// The backend owns the index schema, migration, filtering and pagination.
/** 通过受管辅助进程读取工作区操作账本，显式传入会话范围、工具筛选和数量上限。 */
export async function workspaceOperations(config, root, scope, {limit, tools = [], signal} = {}) {
  const sessions = [...new Set([scope.session_id, ...(scope.session_ids || [])].filter(Boolean))];
  const args = ['-B', '-m', 'server.workspace_operations', '--workspace', root.path,
    '--sessions', JSON.stringify(sessions), '--tools', JSON.stringify(tools), '--limit', String(limit)];
  if (scope.history) args.push('--history');
  if (config.productionConfig) args.push('--config', config.productionConfig);
  const {stdout} = await (config._executePython || executePython)(config.productionPython, args,
    {signal, timeout: 12000, maxBuffer: 2 * 1024 * 1024});
  const result = JSON.parse(stdout);
  if (!Array.isArray(result.operations)) throw new Error('Invalid workspace operation response');
  return result.operations;
}
