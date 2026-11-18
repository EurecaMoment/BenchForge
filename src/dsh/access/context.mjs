import {AsyncLocalStorage} from 'node:async_hooks';

// Each plugin is bundled independently. A process-owned symbol keeps their
// trusted call context shared without making any model argument authoritative.
const key = Symbol.for('benchforge.access.context');
const scopes = globalThis[key] ??= new AsyncLocalStorage();

/** Trusted Host call context; never initialized from model arguments. */
export function runWithAccessScope(scope, operation) {
  return scopes.run(Object.freeze({...scope}), operation);
}

/** Background operations must pass their own persisted, verified scope. */
export function currentAccessScope() { return scopes.getStore(); }
