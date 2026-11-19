/** 创建原生工作台请求适配器，优先调用有类型的远程接口并统一读取、写入与错误处理。 */
export function createWorkbenchRequest(remote) {
  return /** 按视图选用有类型的原生远程读取接口；写入统一使用工作台写方法并传播取消。 */ async (params, {body, signal} = {}) => {
    const query = Object.fromEntries(new URLSearchParams(params));
    const methods = {stage_review: 'stageReview', progress: 'progress', stage_artifacts: 'sceneArtifacts'};
    const method = methods[query.view];
    const {view: _view, ...typedQuery} = query;
    const result = body === undefined
      ? await (method ? remote.benchforgeWorkbench[method](typedQuery, signal) : remote.benchforgeWorkbench.read(query, signal))
      : await remote.benchforgeWorkbench.write(query, body, signal);
    if (!result.ok) {
      signal?.throwIfAborted();
      throw new Error(result.error.message);
    }
    return result.value;
  };
}

/** 创建工作台查询与可见性钩子，统一轮询、取消、页面恢复和结果状态管理。 */
export function createApiHooks({React}) {
  /** 按查询键管理请求数据和刷新状态；活动面板可轮询，离开或卸载时取消未完成请求。 */
  function useRecord(requestWorkbench, params, revision, interval = 0, active = true) {
    const key = params ? new URLSearchParams(params).toString() : '';
    const [record, setRecord] = React.useState({key: '', value: null, error: null, loading: false});
    React.useEffect(/** 管理当前查询的请求、轮询与页面可见监听，清理时取消请求并阻止旧响应写回。 */ () => {
      if (!key) return;
      let stopped = false, timer, controller, inFlight = false;
      /** 执行有超时且防重入的读取，只把仍属于当前查询的响应写入组件状态。 */
      const load = async () => {
        if (stopped || inFlight) return;
        inFlight = true;
        controller = new AbortController();
        const deadline = setTimeout(() => controller.abort(), 16000);
        setRecord(/** 合并同一查询的新结果；场景服务短暂不可用时保留先前可见场景以免界面闪空。 */ (old) => old.key === key ? {...old, loading: !old.value, refreshing: true} :
          {key, value: null, error: null, loading: true, refreshing: true});
        try {
          const value = await requestWorkbench(key, {signal: controller.signal});
          if (!stopped) setRecord(/** 合并同一查询的新结果；场景服务短暂不可用时保留先前可见场景以免界面闪空。 */ (old) => {
            if (value.scenes && old.key === key && old.value?.scenes) {
              const prior = old.value.scenes.runs || [];
              if (value.scenes.status === 'unavailable') value.scenes.runs = prior;
              else value.scenes.runs = (value.scenes.runs || []).map((row) => row.error
                ? {...(prior.find((previous) => previous.run_id === row.run_id) || row), error: row.error} : row);
            }
            return {key, value, error: null, loading: false, refreshing: false, updatedAt: new Date().toISOString()};
          });
        } catch (error) {
          if (!stopped) setRecord(/** 合并同一查询的新结果；场景服务短暂不可用时保留先前可见场景以免界面闪空。 */ (old) => ({...old, key, error: error.message, loading: false, refreshing: false}));
        } finally {
          clearTimeout(deadline); inFlight = false;
          if (!stopped && interval && active && document.visibilityState !== 'hidden') timer = setTimeout(load, interval);
        }
      };
      /** 页面恢复可见时清除旧定时器，并立即刷新活动查询。 */
      const resume = () => {
        clearTimeout(timer);
        if (active && document.visibilityState !== 'hidden') load();
      };
      load();
      if (interval) document.addEventListener('visibilitychange', resume);
      return () => {stopped = true; clearTimeout(timer); controller?.abort(); document.removeEventListener('visibilitychange', resume);};
    }, [requestWorkbench, key, revision, interval, active]);
    return record.key === key ? record : {value: null, error: null, loading: Boolean(key)};
  }

  /** 使用交叉观察器跟踪面板是否可见，卸载时解除观察。 */
  function usePanelVisible(ref) {
    const [visible, setVisible] = React.useState(false);
    React.useEffect(() => {
      const observer = new IntersectionObserver(([entry]) => setVisible(entry.isIntersecting));
      observer.observe(ref.current);
      return () => observer.disconnect();
    }, []);
    return visible;
  }
  return {useRecord, usePanelVisible};
}
