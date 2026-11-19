import fs from 'node:fs/promises';
import path from 'node:path';

export class ApiError extends Error {
  /** 创建带 HTTP 响应状态的工作台访问错误。 */
  constructor(message, status = 400) { super(message); this.status = status; }
}

/** 读取 JSON 文件；文件不存在时返回空值，其余读取或解析错误继续抛出。 */
export async function readJson(file) {
  try { return JSON.parse(await fs.readFile(file, 'utf8')); }
  catch (error) { if (error.code === 'ENOENT') return null; throw error; }
}

/** 检查路径是否存在；仅把不存在错误转换为否。 */
export async function exists(file) {
  try { await fs.access(file); return true; }
  catch (error) { if (error.code === 'ENOENT') return false; throw error; }
}

/** 将根目录身份和相对路径编码为不透明的工作台记录标识。 */
export const token = (root, relative) => Buffer.from(JSON.stringify([root.id, relative])).toString('base64url');
/** 从请求查询参数解析会话范围及是否显式查看工作区历史。 */
export const scopeOf = (url, sessions = []) => ({session_id: url.searchParams.get('session_id') || '', session_ids: sessions,
  history: url.searchParams.get('scope') === 'workspace'});
/** 将当前会话或工作区历史范围转换为后续链接所需的查询参数。 */
export const scopeParams = (scope) => scope.history ? {scope: 'workspace'} : {session_id: scope.session_id};
/** 检查记录上下文是否属于所选会话族；工作区历史范围可直接包含所有记录。 */
export const matchesSession = (context, scope) => scope.history || !!scope.session_id &&
  [scope.session_id, ...(scope.session_ids || [])].some((id) => context?.session_id === id || context?.session_ids?.includes(id));

/** 解析路径的真实位置，并拒绝离开已注册工作区的文件。 */
export async function inside(root, file) {
  const resolved = await fs.realpath(path.resolve(root.path, file));
  const relative = path.relative(root.path, resolved);
  if (relative === '..' || relative.startsWith('..' + path.sep) || path.isAbsolute(relative)) {
    throw new ApiError('Path is outside the registered workspace', 403);
  }
  return resolved;
}

/** 解码工作台记录标识，确认根目录仍已注册并解析受限目录。 */
export async function resolveToken(id, roots) {
  let parts;
  try { parts = JSON.parse(Buffer.from(id || '', 'base64url').toString()); }
  catch { throw new ApiError('Invalid record identity'); }
  if (!Array.isArray(parts) || parts.length !== 2 || parts.some((value) => typeof value !== 'string')) throw new ApiError('Invalid record identity');
  const root = roots.find((item) => item.id === parts[0]);
  if (!root) throw new ApiError('Workspace is no longer registered', 404);
  return {root, directory: await inside(root, parts[1])};
}

/** 检查目录未被场景库策略排除，并沿父目录寻找属于当前会话的请求或状态记录。 */
export async function assertSessionDirectory(root, directory, scope) {
  const policy = await readJson(path.join(root.path, 'scene-library', 'policy.json'));
  if (policy?.enabled && (policy.excluded_paths || []).some((folder) => directory === folder || directory.startsWith(folder + path.sep)))
    throw new ApiError('此产物使用的立方体场景已删除，请重新制作。', 410);
  if (scope.history) return;
  let current = directory;
  while (current !== root.path) {
    const request = await readJson(path.join(current, 'request.json'));
    if (matchesSession(request?.context, scope)) return;
    const state = await readJson(path.join(current, 'state.json'));
    if (matchesSession(state?.context, scope)) return;
    const parent = path.dirname(current);
    if (parent === current) break;
    current = parent;
  }
  throw new ApiError('This artifact is not associated with the current conversation', 403);
}

/** 合并并去重已注册和显式配置的真实根目录，只返回请求明确指定且仍存在的工作区。 */
export async function workspaceRoots(config, registered, requestedPath) {
  const roots = new Map();
  const inputs = [...registered, ...(config.workspacePaths || []).map((value) => ({id: 'path:' + value, path: value, title: path.basename(value)}))];
  for (const input of inputs) {
    try {
      const directory = await fs.realpath(input.path);
      if (!roots.has(directory)) roots.set(directory, {id: String(input.id), path: directory, title: input.title || path.basename(directory)});
    } catch (error) { if (error.code !== 'ENOENT') throw error; }
  }
  const values = [...roots.values()];
  if (!requestedPath) return [];
  let resolved;
  try { resolved = await fs.realpath(requestedPath); }
  catch (error) { if (error.code === 'ENOENT') return []; throw error; }
  return values.filter((root) => root.path === resolved);
}
