import {waitWithSignal} from '../runtime/cancellation.mjs';
import {releaseProcess, releaseProcesses} from './process_cleanup.mjs';
import path from 'node:path';
import {pythonHostDirectory, pythonHostEnvironment} from './python_environment.mjs';

/** Host-owned backend calls; timers and browser reads use the same policy. */
/** 创建通过受管 Python 子进程推进阶段复核的客户端，并统一持有取消与回收责任。 */
export function createStageReviewClient(subprocess, config = {}) {
  const productionConfig = config.productionConfig ? path.resolve(config.productionConfig) : '';
  const lifetime = new AbortController(), active = new Set();
  /** 在请求、超时及宿主寿命约束下查询复核状态，并确保子进程退出后才接受结果。 */
  async function advance(workspace, sessionId, reviewId, _now, callerSignal) {
    const signal = AbortSignal.any([lifetime.signal, AbortSignal.timeout(config.reviewTimeoutMs ?? 15000), ...(callerSignal ? [callerSignal] : [])]);
    const env = pythonHostEnvironment({...config.environment, BENCHFORGE_MANAGED_SUBPROCESS:'1'});
    const python = await subprocess.resolveExecutable(config.python || 'python3', env, signal);
    signal.throwIfAborted();
    const argv = [python, '-B', '-m', 'server.stage_review_cli',
      '--workspace',workspace,'--session',sessionId,'--request',JSON.stringify({action:'status',review_id:reviewId})];
    if (productionConfig) argv.push('--config', productionConfig);
    const handle = subprocess.spawn({argv,
    cwd:pythonHostDirectory,env,signal,graceMs:1000,
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
  return {advance, /** 取消阶段复核客户端并回收其仍在运行的全部子进程。 */ async dispose() {
    lifetime.abort(new DOMException('Stage review host unloaded', 'AbortError'));
    await releaseProcesses(active, config.cleanupTimeoutMs);
  }};
}
