import {captureRunId} from './capture_watch.mjs';

const trackedRoutes = new Set(['/capture','/refine','/start','/extend','/retry']);

/** Once accepted by the service, a submission must always return its identity. */
/** 为已提交采集登记后台续接；追踪登记失败时保留服务回执，避免诱发重复昂贵任务。 */
export async function trackCaptureSubmission(receipt, route, args, exec, tracking) {
  const run_id = receipt.run_id || (route === '/retry' ? captureRunId(receipt.task_id || args.task_id) : '');
  if (!trackedRoutes.has(route) || !run_id) return receipt;
  let watch;
  try {
    const registered = await tracking.register(exec.agent, {run_id,call_id:exec.callId,retry:route === '/retry'});
    watch = registered.watch;
  } catch (error) {
    // The standard tool/result is also a recovery record. Throwing here would
    // hide the already submitted job and invite duplicate expensive captures.
    watch = {background:false,accepted:false,state:'registration_failed',error:String(error.message || error)};
  }
  return {...receipt,run_id,...(receipt.state === 'accepted' ? {state:'submitted'} : {}),
    submission_accepted:true,scene_acceptance:'pending',watch,
    next:watch.accepted ?
      'The host tracks this task automatically and resumes this session on terminal success or failure. Submission is not scene acceptance.' :
      'This task was submitted. Preserve run_id and recover tracking with benchforge_wait(background=true); do not submit a duplicate.'};
}
