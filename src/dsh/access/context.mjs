import {AsyncLocalStorage} from 'node:async_hooks';

// Each plugin is bundled independently. A process-owned symbol keeps their
// trusted call context shared without making any model argument authoritative.
const key = Symbol.for('benchforge.access.context');
const scopes = globalThis[key] ??= new AsyncLocalStorage();

/** Trusted Host call context; never initialized from model arguments. */
/** 在可信宿主调用期间绑定只读访问作用域；作用域只能由宿主建立，不能取自模型参数。 */
export function runWithAccessScope(scope, operation) {
  return scopes.run(Object.freeze({...scope}), operation);
}

/** Background operations must pass their own persisted, verified scope. */
/** 读取当前异步调用的可信访问作用域；后台任务须自行传入已持久化并验证的作用域。 */
export function currentAccessScope() { return scopes.getStore(); }
