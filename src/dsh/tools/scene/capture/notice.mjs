/** Bounded notifications; complete diagnostics remain in the evidence service. */
/** 收集采集错误和任务诊断，形成去重的可读状态通知。 */
export function captureNotice(observation = {}, error) {
  const issues = new Set();
  /** 仅沿已知诊断字段递归收集非空问题文本，避免夹带整个内部对象。 */
  function collect(value) {
    if (!value || typeof value !== 'object') return;
    for (const [key, child] of Object.entries(value)) {
      if (['issues','errors','error','detail'].includes(key)) {
        for (const item of Array.isArray(child) ? child : [child])
          if (typeof item === 'string' && item.trim()) issues.add(item.trim());
      } else if (['failure','scene_review','diagnostics','quality','feedback'].includes(key)) {
        for (const item of Array.isArray(child) ? child : [child]) collect(item);
      }
    }
  }
  for (const task of observation.tasks || []) collect(task);
  if (error) issues.add(String(error));
  const all = [...issues];
  return {
    run_id:observation.run_id, state:observation.state,
    issues:all.slice(0,12).map(value=>value.length > 600 ? value.slice(0,600)+'…' : value),
    issue_count:all.length,
    diagnostics_truncated:all.length > 12 || all.some(value => value.length > 600),
    tasks:(observation.tasks || []).map(task=>{
      const failure=task.failure;
      const progress=failure?.scene_review?.repair_progress;
      const summary=progress && {decision:progress.decision,round:progress.round,baseline:progress.baseline,
        candidate:progress.candidate,next_base:progress.next_base};
      const recoverable=['capture_execution','capture_transport'].includes(failure?.stage) ||
        (task.phase === 'review' && ['TimeoutError','URLError','ConnectionError','RemoteDisconnected',
          'ConnectionResetError','ConnectionAbortedError','OSError'].includes(failure?.error));
      const retry=task.state === 'FAILED_FINAL' && recoverable && !failure.scene_review &&
        captureRunMatches(observation.run_id,task.id);
      return {id:task.id,state:task.state,records:task.records,...(summary && {repair_progress:summary}),
        ...(retry && {recovery:{tool:'benchforge_retry',arguments:{task_id:task.id}}})};
    }),
    detail:{tool:'benchforge_scene_status',run_id:observation.run_id,detail:true},
  };
}

/** 恢复动作只能引用当前回执内的原始工作项。 */
function captureRunMatches(run, task) {
  return typeof task === 'string' && task.split('.')[0] === run && /^sf_[\w-]+\.scene\d+$/.test(task);
}
