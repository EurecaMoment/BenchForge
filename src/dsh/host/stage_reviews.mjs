/** Host-owned backend calls; timers and browser reads use the same policy. */
export function createStageReviewClient(subprocess, config = {}) {
  const lifetime = new AbortController(), active = new Set();
  async function advance(workspace, sessionId, reviewId) {
    const signal = AbortSignal.any([lifetime.signal, AbortSignal.timeout(15000)]);
    const env = {...config.environment, PYTHONIOENCODING:'utf-8', BENCHFORGE_MANAGED_SUBPROCESS:'1'};
    const python = await subprocess.resolveExecutable(config.python || 'python3', env, signal);
    signal.throwIfAborted();
    const handle = subprocess.spawn({argv:[python, '-B', '-m', 'server.stage_review_cli',
      '--workspace',workspace,'--session',sessionId,'--request',JSON.stringify({action:'status',review_id:reviewId})],
    cwd:workspace,env,signal,graceMs:1000,
    stdio:{stdout:{maxBytes:128000},stderr:{maxBytes:16000}}});
    active.add(handle);
    try {
      const outcome = await handle.done;
      signal.throwIfAborted();
      const stdout = handle.collected.stdout.readFrom(0);
      if (outcome.exitCode !== 0) throw new Error(handle.collected.stderr.readFrom(0).text || 'Stage review check failed to run');
      if (stdout.lossy) throw new Error('Stage review receipt exceeded its output limit');
      return JSON.parse(stdout.text);
    } finally {
      handle.terminate();
      await handle.waitForExit();
      active.delete(handle);
    }
  }
  return {advance, async dispose() {
    lifetime.abort(new DOMException('Stage review host unloaded', 'AbortError'));
    for (const handle of active) handle.terminate();
    await Promise.allSettled([...active].map(handle => handle.waitForExit()));
  }};
}
