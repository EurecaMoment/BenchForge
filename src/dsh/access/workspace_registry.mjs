import path from 'node:path';
import {within} from './workspaces.mjs';

/** 只撤销旧插件为单个会话登记的默认侧栏条目；文件和 DSH 会话由各自的宿主管理。 */
export async function retireSessionRegistrations(store, registry) {
  const byPath = new Map([...store.table.entries()].map(([, row]) => [row.rootPath, row]));
  for (const entry of registry.list()) {
    const row = byPath.get(entry.path);
    if (!row || row.registeredProject || row.state !== 'active' || row.hostId !== store.hostId || row.ownerId !== store.ownerId ||
        row.rootPath !== path.join(store.workspaceRoot, row.workspaceId) ||
        entry.title !== 'BenchForge' || entry.sessionIds.length !== 1 || entry.sessionIds[0] !== row.sessionId) continue;
    // 旧工作台工件链接包含注册表 ID。先保存 ID，重试迁移时仍可解析已有链接。
    await store.table.put(row.sessionId, {...row, artifactRootId: entry.id});
    await registry.delete(entry.id);
  }
}

/** 从受管绑定提供当前会话族的文件根目录，其他显式工作区继续由 DSH 管理。 */
export function lookupWorkspaceRoots(store, registry, sessionIds, configuredPaths = []) {
  const publicRoots = [...registry.list(), ...configuredPaths.map(directory => ({
    id: 'path:' + directory, path: directory, title: path.basename(directory),
  }))];
  const boundRoots = new Map();
  for (const id of new Set(sessionIds)) {
    const row = id && store.table.get(id);
    if (!row || row.state !== 'active' || row.hostId !== store.hostId || row.ownerId !== store.ownerId ||
        (!row.registeredProject && row.rootPath !== path.join(store.workspaceRoot, row.workspaceId))) continue;
    const registered = publicRoots.find(entry => entry.path === row.rootPath);
    boundRoots.set(row.rootPath, {id: row.artifactRootId || registered?.id || row.workspaceId,
      path: row.rootPath, title: registered?.title || 'BenchForge'});
  }
  return [...boundRoots.values(), ...publicRoots.filter(root => !boundRoots.has(root.path) && !within(store.workspaceRoot, root.path))];
}
