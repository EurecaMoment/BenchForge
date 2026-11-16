/** Bounded notifications; complete diagnostics remain in the evidence service. */
export function captureNotice(observation = {}, error) {
  const issues = new Set();
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
    before_repair:'Read the full detail result, combine all independent native/visual issues, and batch-preflight one complete candidate. This notice is only a summary.',
    tasks:(observation.tasks || []).map(task=>({id:task.id,state:task.state,records:task.records})),
    detail:{tool:'benchforge_scene_status',run_id:observation.run_id,detail:true},
  };
}
