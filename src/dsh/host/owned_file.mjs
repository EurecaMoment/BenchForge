import fs from 'node:fs/promises';
import {constants} from 'node:fs';
import path from 'node:path';
import {ApiError} from './session_scope.mjs';

const contains = (root, file) => {
  const relative = path.relative(root, file);
  return relative === '' || relative !== '..' && !relative.startsWith('..' + path.sep) && !path.isAbsolute(relative);
};

/** Pin an approved regular file before streaming, without reopening its path. */
export async function openOwnedFile(root, file, signal) {
  const canonicalRoot = await fs.realpath(root);
  const candidate = path.resolve(file);
  if (!contains(canonicalRoot, candidate)) throw new ApiError('File is outside its owning workspace', 403);
  signal?.throwIfAborted();
  let handle;
  try {
    if (process.platform === 'linux') {
      handle = await fs.open('/', constants.O_RDONLY | constants.O_DIRECTORY | constants.O_NOFOLLOW);
      for (const part of candidate.split('/').filter(Boolean)) {
        signal?.throwIfAborted();
        const next = await fs.open(`/proc/self/fd/${handle.fd}/${part}`,
          constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
        await handle.close();
        handle = next;
      }
    } else {
      // The strict execution provider requires Linux; retain desktop previews.
      if (!contains(canonicalRoot, await fs.realpath(candidate))) throw new ApiError('File leaves its workspace', 403);
      handle = await fs.open(candidate, constants.O_RDONLY | (constants.O_NOFOLLOW || 0));
    }
    const stat = await handle.stat();
    if (!stat.isFile()) throw new ApiError('The artifact is not a regular file', 404);
    signal?.throwIfAborted();
    return {handle, stat};
  } catch (error) {
    await handle?.close();
    if (['ELOOP', 'ENOTDIR'].includes(error.code)) throw new ApiError('Artifact path changed or follows a link', 403);
    throw error;
  }
}
