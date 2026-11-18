import fs from 'node:fs/promises';
import path from 'node:path';
import {randomUUID} from 'node:crypto';
import {AccessError} from './workspaces.mjs';

/** Copy only packaged documentation/support files, never source directory grants. */
export async function publishResources(source, resourceRoot, label, extensions) {
  const target = path.join(resourceRoot, 'shared', label + '-' + randomUUID());
  await fs.mkdir(target, {recursive: true, mode: 0o700});
  let count = 0;
  async function copy(directory, destination) {
    for (const entry of await fs.readdir(directory, {withFileTypes: true})) {
      const from = path.join(directory, entry.name), to = path.join(destination, entry.name);
      if (entry.isSymbolicLink()) throw new AccessError('Packaged resources cannot contain symbolic links');
      if (entry.isDirectory()) { await fs.mkdir(to); await copy(from, to); }
      else if (entry.isFile() && extensions.test(entry.name)) {
        const stat = await fs.stat(from);
        if (++count > 256 || stat.size > 2 * 1024 * 1024) throw new AccessError('Packaged resource budget exceeded');
        await fs.copyFile(from, to, fs.constants.COPYFILE_EXCL);
        await fs.chmod(to, 0o444);
      }
    }
  }
  await copy(source, target);
  return target;
}
