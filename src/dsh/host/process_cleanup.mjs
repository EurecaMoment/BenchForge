import {waitWithSignal} from '../runtime/cancellation.mjs';
import {HarnessError} from '@deepseek-ai/dsh-error';

export class ProcessCleanupError extends HarnessError {
  constructor(timeoutMs) {
    super(`Process cleanup was not confirmed within ${timeoutMs} ms. The command result is not accepted; process ownership is retained for cleanup.`, 'BENCHFORGE_PROCESS_CLEANUP_PENDING');
  }
}

/** Keep failed handles owned; bounded waiting never turns unknown cleanup into success. */
export async function releaseProcess(handle, {active, timeoutMs = 5000} = {}) {
  handle.terminate();
  const deadline = new AbortController();
  const timeout = setTimeout(() => deadline.abort(new ProcessCleanupError(timeoutMs)), timeoutMs);
  try {
    const exited = await waitWithSignal(handle.waitForExit(deadline.signal), deadline.signal);
    if (exited === false) throw new ProcessCleanupError(timeoutMs);
    active?.delete(handle);
  } finally {
    clearTimeout(timeout);
  }
}

export async function releaseProcesses(active, timeoutMs) {
  const results = await Promise.allSettled([...active].map(handle => releaseProcess(handle, {active, timeoutMs})));
  const errors = results.filter(result => result.status === 'rejected').map(result => result.reason);
  if (errors.length) throw new AggregateError(errors, 'Some BenchForge processes still require cleanup');
}
