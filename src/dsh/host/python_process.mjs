import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {pythonHostDirectory, pythonHostEnvironment} from './python_environment.mjs';

const execute = promisify(execFile);

// The process boundary owns JSON encoding, independent of the host locale.
export function executePython(python, args, options = {}) {
  return execute(python, args, {...options, cwd: pythonHostDirectory, encoding: 'utf8',
    env: pythonHostEnvironment({...process.env, ...options.env})});
}

// Foreground queries and preview encoders belong to the Host activation too.
// Retain execFile's child handle for the plan editor's stdin protocol.
export function createPythonProcessScope() {
  const lifetime = new AbortController(), children = new Map();
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
  return {execute, async dispose() {
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
