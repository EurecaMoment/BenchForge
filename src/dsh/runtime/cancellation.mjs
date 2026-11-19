/** Cancel a caller's wait without cancelling work owned by a different caller. */
/** 等待异步操作并响应外部取消；无论完成还是取消，都移除事件监听。 */
export function waitWithSignal(operation, signal) {
  if (!signal) return Promise.resolve(operation);
  return new Promise((resolve, reject) => {
    /** 移除取消监听并以原始取消原因拒绝等待结果。 */
    const abort = () => {signal.removeEventListener('abort', abort);reject(signal.reason);};
    signal.addEventListener('abort', abort, {once: true});
    Promise.resolve(operation).then(resolve, reject).finally(() => signal.removeEventListener('abort', abort));
    if (signal.aborted) abort();
  });
}
