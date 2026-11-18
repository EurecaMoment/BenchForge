/** Cancel a caller's wait without cancelling work owned by a different caller. */
export function waitWithSignal(operation, signal) {
  if (!signal) return Promise.resolve(operation);
  return new Promise((resolve, reject) => {
    const abort = () => {signal.removeEventListener('abort', abort);reject(signal.reason);};
    signal.addEventListener('abort', abort, {once: true});
    Promise.resolve(operation).then(resolve, reject).finally(() => signal.removeEventListener('abort', abort));
    if (signal.aborted) abort();
  });
}
