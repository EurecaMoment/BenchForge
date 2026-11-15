import path from 'node:path';
import {readLatestStageReview} from './reviews.mjs';
import {stageName} from './stages.mjs';

export const name = 'benchforge-review-policy';
export const inject = ['tools'];

const pending = new Set(['waiting_user', 'paused', 'needs_input']);
const reads = new Set(['read', 'read_image', 'read_file', 'list_directory', 'list_files', 'glob', 'grep',
  'search', 'web_search', 'web_fetch', 'fetch', 'present', 'todo', 'todo_write', 'todowrite', 'think',
  'ask_user_question', 'skill', 'read_resource', 'list_resources',
  'benchforge_pipeline_catalog', 'benchforge_pipeline_status', 'benchforge_pipeline_artifacts',
  'benchforge_scene_status', 'benchforge_scene_evidence', 'benchforge_wait', 'benchforge_scene_catalog']);
const writes = new Set(['write', 'edit', 'str_replace_editor']);
const inspections = new Set(['capabilities', 'health', 'healthz', 'status', 'catalog', 'api_catalog',
  'get_job', 'watch_job', 'download_artifact']);

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
      ['status', 'wait', 'pause', 'acknowledge', 'open'].includes(args.action || 'status')) return;
  if (/^benchforge_model_isaacsim_(grpc|http)$/.test(exec.name) && inspections.has(args.action)) return;
  return {
    reason: `${review.stage} ${stageName(review.stage)} 的审核尚未结束（${review.state}）。先展示真实产物并用 benchforge_stage_review 等待或处理用户指令。可读取、检查和编辑 tasks/ 下的准备稿；依赖该阶段的生产、脚本执行和仿真提交须等审核放行。S2 选取或新建并验收场景；S3 采集轨迹、观测及标注。`,
    info: {name: 'ProductionReviewError', code: 'STAGE_REVIEW_PENDING'},
  };
}

export function createReviewGuard({readReview = readLatestStageReview, agents, sessions} = {}) {
  return exec => {
    let agent = exec.agent;
    if (!agent?.session?.header?.cwd) return;
    const seen = new Set();
    let owner = agent.id;
    while (agent?.session?.header?.parentSession && !seen.has(owner)) {
      seen.add(owner);
      owner = agent.session.header.parentSession;
      const parent = agents?.get(owner), session = parent?.session || sessions?.get(owner);
      if (!session?.header?.cwd) {
        const denial = reviewDenial(exec, {stage: '', state: 'needs_input'}, agent.session.header.cwd);
        return denial && {...denial, reason: 'The parent review owner is not loaded. Resume the parent conversation to confirm the production stage; reads and draft edits remain available.'};
      }
      agent = parent || {id: owner, session};
    }
    const workspace = agent.session.header.cwd;
    try {return reviewDenial(exec, readReview(workspace, owner), workspace);}
    catch {
      // Allow inspection when storage is unavailable, but do not bypass a
      // potentially active review for a submission or shell.
      return reviewDenial(exec, {stage: '', state: 'needs_input'}, workspace);
    }
  };
}

export function apply(ctx) {
  ctx.tools.guard(createReviewGuard({agents: ctx.get('agents'), sessions: ctx.get('sessions')}));
}
