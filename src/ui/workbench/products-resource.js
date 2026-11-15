const waitForRefresh = (signal, delay) => new Promise((resolve) => {
  const finish = () => {
    clearTimeout(timer);
    signal.removeEventListener('abort', finish);
    resolve();
  };
  const timer = setTimeout(finish, delay);
  signal.addEventListener('abort', finish, {once: true});
  if (signal.aborted) finish();
});

function failure(error) {
  return {ok: false, error: {message: error?.message || 'Conversation products are unavailable'}};
}

/** The client resource owns refresh and cancellation; UI components only subscribe. */
export function createProductsResource(remote) {
  return {
    protocol: 'benchforge-workbench',
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

export function productsResourceAddress(sessionId, workspacePath) {
  if (!sessionId || !workspacePath) return '';
  const query = new URLSearchParams({workspace_path: workspacePath});
  return `dsh-resource://benchforge-workbench/products/${encodeURIComponent(sessionId)}?${query}`;
}
