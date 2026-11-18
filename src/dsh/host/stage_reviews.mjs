import {waitWithSignal} from '../runtime/cancellation.mjs';
import {releaseProcess, releaseProcesses} from './process_cleanup.mjs';
import path from 'node:path';

/** Host-owned backend calls; timers and browser reads use the same policy. */
export function createStageReviewClient(subprocess, config = {}) {
  const productionConfig = config.productionConfig ? path.resolve(config.productionConfig) : '';
  const lifetime = new AbortController(), active = new Set();
  async function advance(workspace, sessionId, reviewId, _now, callerSignal) {
    const signal = AbortSignal.any([lifetime.signal, AbortSignal.timeout(config.reviewTimeoutMs ?? 15000), ...(callerSignal ? [callerSignal] : [])]);
    const env = {...config.environment, PYTHONIOENCODING:'utf-8', BENCHFORGE_MANAGED_SUBPROCESS:'1'};
    const python = await subprocess.resolveExecutable(config.python || 'python3', env, signal);
    signal.throwIfAborted();
    const argv = [python, '-B', '-m', 'server.stage_review_cli',
      '--workspace',workspace,'--session',sessionId,'--request',JSON.stringify({action:'status',review_id:reviewId})];
    if (productionConfig) argv.push('--config', productionConfig);
    const handle = subprocess.spawn({argv,
    cwd:workspace,env,signal,graceMs:1000,
    stdio:{stdout:{maxBytes:128000},stderr:{maxBytes:16000}}});
    active.add(handle);
    try {
      const outcome = await waitWithSignal(handle.done, signal);
      signal.throwIfAborted();
      const stdout = handle.collected.stdout.readFrom(0);
      if (outcome.exitCode !== 0) throw new Error(handle.collected.stderr.readFrom(0).text || 'Stage review check failed to run');
      if (stdout.lossy) throw new Error('Stage review receipt exceeded its output limit');
      return JSON.parse(stdout.text);
    } finally {
      try {
        await releaseProcess(handle, {active, timeoutMs: config.cleanupTimeoutMs});
      } catch (error) {
        config.onCleanupError?.(error);
        if (!signal.aborted) throw error;
      }
      signal.throwIfAborted();
    }
  }
  return {advance, async dispose() {
    lifetime.abort(new DOMException('Stage review host unloaded', 'AbortError'));
    await releaseProcesses(active, config.cleanupTimeoutMs);
  }};
}
