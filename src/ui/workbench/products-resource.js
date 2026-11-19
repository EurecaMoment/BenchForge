/** 等待下一次资源刷新间隔；取消时立即解除等待并清理定时器与监听。 */
const waitForRefresh = (signal, delay) => new Promise((resolve) => {
  /** 结束刷新等待，并移除当前定时器及取消事件监听。 */
  const finish = () => {
    clearTimeout(timer);
    signal.removeEventListener('abort', finish);
    resolve();
  };
  const timer = setTimeout(finish, delay);
  signal.addEventListener('abort', finish, {once: true});
  if (signal.aborted) finish();
});

/** 将资源读取异常转换为统一的可展示失败结果。 */
function failure(error) {
  return {ok: false, error: {message: error?.message || 'Conversation products are unavailable'}};
}

/** The client resource owns refresh and cancellation; UI components only subscribe. */
/** 创建会话产物资源提供器，持续刷新并在取消时结束读取。 */
export function createProductsResource(remote) {
  return {
    protocol: 'benchforge-workbench',
    /** 校验资源地址中的会话身份，按序输出产物快照或可读错误并等待下一次刷新。 */
    async *open(address, {signal}) {
      let query;
      try {
        const url = new URL(address);
        const [, resource, sessionId] = url.pathname.split('/');
        if (resource !== 'products' || !/^session-[A-Za-z0-9-]+$/.test(sessionId || '')) {
          yield failure(new Error('Conversation products address is invalid'));
          return;
        }
        query = {session_id: sessionId, workspace_path: url.searchParams.get('workspace_path') || ''};
      } catch (error) {
        yield failure(error);
        return;
      }
      while (!signal.aborted) {
        try {yield await remote.benchforgeWorkbench.products(query, signal);}
        catch (error) {
          if (signal.aborted) return;
          yield failure(error);
        }
        await waitForRefresh(signal, 4000);
      }
    },
  };
}

/** 为有效会话和工作区生成稳定的原生资源地址，缺少归属信息时返回空地址。 */
export function productsResourceAddress(sessionId, workspacePath) {
  if (!sessionId || !workspacePath) return '';
  const query = new URLSearchParams({workspace_path: workspacePath});
  return `dsh-resource://benchforge-workbench/products/${encodeURIComponent(sessionId)}?${query}`;
}
