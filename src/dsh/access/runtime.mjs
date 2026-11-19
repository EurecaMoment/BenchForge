import fs from 'node:fs/promises';
import {resolveRgPath} from '@deepseek-ai/dsh-util-ripgrep';

/** System runtime files only; application dependencies need explicit grants. */
/** 解析当前平台运行宿主和检索工具所需的系统只读路径，跳过不存在的候选并去重。 */
export async function systemReadRoots(platform = process.platform, executable = process.execPath) {
  const candidates = platform === 'linux'
    ? ['/usr/bin', '/bin', '/usr/lib', '/lib', '/lib64', '/etc/ld.so.cache', executable, await resolveRgPath()]
    : [executable];
  const roots = [];
  for (const candidate of candidates) {
    try { roots.push(await fs.realpath(candidate)); }
    catch (error) { if (error.code !== 'ENOENT') throw error; }
  }
  return [...new Set(roots)];
}
