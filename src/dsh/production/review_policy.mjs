import path from 'node:path';
import {createStageReviewReader,reviewReadConfig} from './reviews.mjs';
import {launchEnvironmentOf} from '@deepseek-ai/dsh-launch-environment';
import Schema from '@deepseek-ai/schemastery';
import {HarnessError} from '@deepseek-ai/dsh-error';
import {stageName} from './stages.mjs';

export const name = 'benchforge-review-policy';
export const inject = ['tools'];
export const Config = Schema.object(reviewReadConfig);

const pending = new Set(['waiting_user', 'paused', 'needs_input', 'check_failed']);
const reads = new Set(['read', 'read_image', 'read_file', 'list_directory', 'list_files', 'glob', 'grep',
  'search', 'web_search', 'web_fetch', 'fetch', 'present', 'todo', 'todo_write', 'todowrite', 'think',
  'ask_user_question', 'skill', 'read_resource', 'list_resources',
  'benchforge_pipeline_catalog', 'benchforge_pipeline_status', 'benchforge_pipeline_artifacts',
  'benchforge_scene_status', 'benchforge_scene_evidence', 'benchforge_wait', 'benchforge_scene_catalog',
  'benchforge_scene_preflight']);
const writes = new Set(['write', 'edit', 'str_replace_editor']);
const inspections = new Set(['guide', 'capabilities', 'status', 'catalog', 'assets', 'wait', 'download']);

function draftWrite(exec, workspace) {
  if (!writes.has(exec.name)) return false;
  const args = exec.arguments || {}, target = args.file_path || args.path;
  if (typeof target !== 'string') return false;
  const api = /^[A-Za-z]:[\\/]/.test(workspace) ? path.win32 : path.posix;
  const relative = api.relative(workspace, api.resolve(workspace, target));
  // Drafts stay in task/preparation folders; a tool must not edit the persisted
  // review ledger, frozen operation receipts or runtime configuration to resume.
  return !api.isAbsolute(relative) && /^(tasks|synthesis)[\\/]/.test(relative);
}

export function reviewDenial(exec, review, workspace) {
  if (!review || !pending.has(review.state)) return;
  const args = exec.arguments || {};
  if (exec.name === 'str_replace_editor' && args.command === 'view') return;
  if (reads.has(exec.name) || draftWrite(exec, workspace)) return;
  if (exec.name === 'benchforge_cancel') return; // Stopping existing work never advances a stage.
  if (exec.name === 'benchforge_pipeline_plan') return; // Validated drafts/revisions are domain-owned.
  if (exec.name === 'benchforge_stage_review' &&
      ['status', 'wait', 'pause', 'acknowledge', 'open', 'retry'].includes(args.action || 'status')) return;
  if (exec.name === 'benchforge_isaac_sim' && (inspections.has(args.action) || args.action === 'cancel')) return;
  const progress = review.state === 'check_failed'
    ? `产物自检失败：${review.check_failure?.error || '读取检查结果并修复后 retry'}。`
    : review.state === 'paused' ? '用户已暂停，须用户继续后才能推进。'
    : review.state === 'needs_input' ? '有待处理的用户指令，落实后再 acknowledge。'
    : `已完成自检 ${review.checks_completed || 0}/3、等待 ${review.waits_completed || 0}/3，本轮剩余 ${review.remaining_seconds || 0} 秒；三轮通过后由后端自动继续。`;
  return {
    reason: `${review.stage} ${stageName(review.stage)} 的审核尚未结束（${review.state}）。${progress}展示真实产物并用 benchforge_stage_review 读取、等待或处理用户指令。可读取和编辑 tasks/ 下的准备稿，依赖该阶段的生产、脚本及仿真提交须等放行。`,
    info: {name: 'ProductionReviewError', code: 'STAGE_REVIEW_PENDING'},
  };
}

export function createReviewPolicy({readReview, agents, sessions} = {}) {
  return async exec => {
    let agent = exec.agent;
    if (!agent?.session?.header?.cwd) return;
    // Draft ownership follows the executing child; stage approval follows its
    // parent chain. Independent child roots need not be nested in that parent.
    const executionWorkspace = agent.session.header.cwd;
    const seen = new Set();
    let owner = agent.id;
    while (agent?.session?.header?.parentSession && !seen.has(owner)) {
      seen.add(owner);
      owner = agent.session.header.parentSession;
      const parent = agents?.get(owner), session = parent?.session || sessions?.get(owner);
      if (!session?.header?.cwd) {
        const denial = reviewDenial(exec, {stage: '', state: 'needs_input'}, executionWorkspace);
        return denial && {...denial, reason: 'The parent review owner is not loaded. Resume the parent conversation to confirm the production stage; reads and draft edits remain available.'};
      }
      agent = parent || {id: owner, session};
    }
    const workspace = agent.session.header.cwd;
    if(!reviewDenial(exec,{stage:'',state:'needs_input'},executionWorkspace))return;
    try {return reviewDenial(exec, await readReview(workspace, owner, Date.now(), exec.signal), executionWorkspace);}
    catch (error) {
      exec.signal?.throwIfAborted();
      // Allow inspection when storage is unavailable, but do not bypass a
      // potentially active review for a submission or shell.
      const denial=reviewDenial(exec, {stage: '', state: 'needs_input'}, executionWorkspace);
      return denial && {...denial,reason:`Cannot verify the current stage review: ${error.message}. Production stays stopped until the stored review can be read; inspection remains available.`,
        info:{name:'StageReviewStorageError',code:error.code||'BENCHFORGE_REVIEW_STORAGE_UNAVAILABLE'}};
    }
  };
}

export function apply(ctx,config) {
  const environment=launchEnvironmentOf(ctx);
  const reader=createStageReviewReader({...config,getEnvironmentValue:name=>environment.get(name)?.value});
  const review=createReviewPolicy({readReview:reader.latest,agents:ctx.get('agents'),sessions:ctx.get('sessions')});
  // Tool guards are synchronous. Read at the async dispatch boundary, after
  // approval, so a paused review cannot be bypassed by a cached decision.
  ctx.on('tools/execute',async(exec,next)=>{
    const denial=await review(exec);
    exec.signal.throwIfAborted();
    if(denial)throw new HarnessError(denial.reason,denial.info.code);
    return next();
  });
}
