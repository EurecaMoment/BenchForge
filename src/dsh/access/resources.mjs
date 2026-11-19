import fs from 'node:fs/promises';
import path from 'node:path';
import {randomUUID} from 'node:crypto';
import {AccessError} from './workspaces.mjs';

/** Copy only packaged documentation/support files, never source directory grants. */
/** 将随包资源复制到新的只读发布目录；拒绝符号链接并限制文件数和单文件体积。 */
export async function publishResources(source, resourceRoot, label, extensions) {
  const target = path.join(resourceRoot, 'shared', label + '-' + randomUUID());
  await fs.mkdir(target, {recursive: true, mode: 0o700});
  let count = 0;
  /** 递归复制符合扩展名限制的普通资源文件，并将副本设为只读。 */
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
