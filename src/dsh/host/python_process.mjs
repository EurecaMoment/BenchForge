import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {pythonHostDirectory, pythonHostEnvironment} from './python_environment.mjs';

const execute = promisify(execFile);

// The process boundary owns JSON encoding, independent of the host locale.
/** 在宿主专用目录中使用可信环境执行 Python，返回异步执行结果。 */
export function executePython(python, args, options = {}) {
  return execute(python, args, {...options, cwd: pythonHostDirectory, encoding: 'utf8',
    env: pythonHostEnvironment({...process.env, ...options.env})});
}

// Foreground queries and preview encoders belong to the Host activation too.
// Retain execFile's child handle for the plan editor's stdin protocol.
/** 创建统一持有 Python 子进程的作用域，使宿主卸载时能够取消并等待所有进程结束。 */
export function createPythonProcessScope() {
  const lifetime = new AbortController(), children = new Map();
  /** 合并调用取消信号与作用域寿命，并登记子进程直至收到关闭事件。 */
  function execute(python, args, options = {}) {
    lifetime.signal.throwIfAborted();
    const signal = options.signal ? AbortSignal.any([lifetime.signal, options.signal]) : lifetime.signal;
    const result = executePython(python, args, {...options, signal});
    const child = result.child;
    const closed = new Promise((resolve) => child.once('close', resolve));
    children.set(child, closed);
    closed.then(() => children.delete(child));
    return result;
  }
  return {execute, /** 取消作用域内的 Python 调用，宽限期后强制结束残留进程并等待关闭。 */ async dispose() {
    lifetime.abort(new DOMException('Workbench Host unloaded', 'AbortError'));
    const pending = [...children.entries()];
    const kill = setTimeout(() => {
      for (const [child] of pending) if (children.has(child)) child.kill('SIGKILL');
    }, 1000);
    kill.unref();
    try {await Promise.all(pending.map(([, closed]) => closed));}
    finally {clearTimeout(kill);}
  }};
}
