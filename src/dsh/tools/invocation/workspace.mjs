import fs from 'node:fs/promises';
import path from 'node:path';

const within = (root, file) => {
  const relative = path.relative(root, file);
  return relative !== '..' && !relative.startsWith('..' + path.sep) && !path.isAbsolute(relative);
};
const missing = error => ['ENOENT', 'ENOTDIR'].includes(error.code);

/** Repair a nonexistent workspace ancestor, never a filename or arbitrary path. */
export async function workspaceReference(file, workspace, signal) {
  signal?.throwIfAborted();
  if (typeof file !== 'string' || !workspace || !path.isAbsolute(file) || !path.isAbsolute(workspace)) return;
  if (file.split(/[\\/]/).some(part => part === '..')) return;
  const root = path.resolve(workspace);
  if (within(root, path.resolve(file))) return;
  try {
    await fs.stat(file);
    return; // Existing references keep their identity, even outside this workspace.
  } catch (error) {
    if (!missing(error)) throw error;
  }
  const parts = root.slice(path.parse(root).root.length).split(path.sep);
  // Three exact trailing directory components identify the workspace; no fuzzy
  // filename search, global filesystem scan, or deployment-specific spelling.
  if (parts.length < 3) return;
  const anchor = path.sep + parts.slice(-3).join(path.sep) + path.sep;
  const normalized = file.replaceAll(path.sep === '\\' ? '/' : '\\', path.sep);
  const index = normalized.indexOf(anchor);
  if (index < 0 || index !== normalized.lastIndexOf(anchor)) return;
  const relative = normalized.slice(index + anchor.length);
  const candidate = path.resolve(root, relative);
  if (!relative || !within(root, candidate)) return;
  try {
    const [actualRoot, actualFile, stat] = await Promise.all([fs.realpath(root), fs.realpath(candidate), fs.stat(candidate)]);
    signal?.throwIfAborted();
    if (within(actualRoot, actualFile) && stat.isFile()) return {absolute: candidate, relative};
  } catch (error) {
    if (!missing(error)) throw error;
  }
}

/** Bind relative image references to the session rather than retyping its root. */
export async function resolveReadArguments(name, args, workspace, signal) {
  if (!['read_image', 'read'].includes(name) || !args || typeof args !== 'object') return args;
  const reference = await workspaceReference(args.file_path, workspace, signal);
  return reference ? {...args, file_path: reference.absolute} : args;
}
