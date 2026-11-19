import path from 'node:path';
import {fileURLToPath} from 'node:url';

// Trusted CLI code must never import from the conversation's writable cwd.
// This directory belongs to the installed Host bundle in source and releases.
export const pythonHostDirectory = fileURLToPath(new URL('.', import.meta.url));

/** 构造可信 Python 启动环境，启用 UTF-8 和安全导入，并拒绝相对 PYTHONPATH 项。 */
export function pythonHostEnvironment(environment = {}) {
  const env = {...environment, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1',
    PYTHONNOUSERSITE: '1', PYTHONSAFEPATH: '1'};
  const searchPath = env.PYTHONPATH ?? process.env.PYTHONPATH;
  if (searchPath !== undefined) {
    const roots = searchPath.split(path.delimiter).filter(Boolean);
    if (roots.some(root => !path.isAbsolute(root)))
      throw new Error('Trusted Python imports require absolute PYTHONPATH entries');
    // Empty entries mean cwd in Python. Remove them even on older interpreters
    // that do not implement PYTHONSAFEPATH; their cwd is still Host-owned.
    env.PYTHONPATH = roots.join(path.delimiter);
  }
  return env;
}
