import path from 'node:path';
import {z} from 'zod';

export const ARTIFACT_PROJECTION = 'benchforgeArtifacts';
const Stage = z.enum(['S1', 'S2', 'S3', 'S4', 'S5']).nullable();
const FileReference = z.object({
  path: z.string(), title: z.string(), stage: Stage, source: z.enum(['tool', 'mutation', 'present']),
  seq: z.number(),
});
export const ArtifactView = z.object({
  revision: z.number(), workspaces: z.array(z.string()), primaryWorkspace: z.string().nullable(),
  files: z.array(FileReference), captureIds: z.array(z.string()),
});
const Call = z.object({name: z.string(), workspace: z.string(), file: z.string().nullable()});
const State = z.object({cwd: z.string(), calls: z.record(z.string(), Call), view: ArtifactView});
const OUTPUT_FILES = new Map([
  ['plan', ['S1', '评测计划']], ['brief', ['S1', '评测需求']], ['plan_path', ['S1', '评测计划']],
  ['scene_program', ['S2', '场景程序']], ['scene_program_path', ['S2', '场景程序']],
  ['image_path', ['S2', '场景图']], ['video_path', ['S2', '场景视频']], ['usd_path', ['S2', 'USD 场景']],
  ['annotations', ['S3', '标注数据']], ['annotation_path', ['S3', '标注数据']],
  ['annotations_path', ['S3', '标注数据']], ['questions', ['S4', '题目数据']],
  ['questions_path', ['S4', '题目数据']], ['dataset_path', ['S4', '数据集']],
  ['report', ['S5', '评测报告']], ['report_path', ['S5', '评测报告']], ['quality_report', ['S5', '质量报告']],
  ['public_bundle', ['S5', '公开数据包']], ['authority_bundle', ['S5', '真值数据包']],
  ['result_path', [null, '执行结果']], ['output_path', [null, '生成文件']], ['output_file', [null, '生成文件']],
  ['receipt_path', [null, '执行回执']], ['manifest_path', [null, '产物清单']], ['authority_path', [null, '真值数据']],
]);
const STEP_STAGES = {scene: 'S2', capture: 'S2', annotations: 'S3', questions: 'S4', package: 'S5'};
const OUTPUT_CONTAINERS = new Set(['result', 'artifact', 'outputs', 'watch']);
const object = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);
const text = (value) => typeof value === 'string' && value.trim() ? value : null;
const filesystem = (cwd) => /^[A-Za-z]:[\\/]/.test(cwd) ? path.win32 : path.posix;
const resolve = (cwd, file) => {
  const api = filesystem(cwd);
  return text(file) && (api.isAbsolute(file) || cwd) ? api.resolve(cwd || '/', file) : null;
};

function argumentsOf(raw) {
  try {const value = JSON.parse(raw); return object(value) ? value : null;}
  catch {return null;}
}

function mutationFile(name, args) {
  if (name === 'write' && typeof args.content === 'string') return text(args.file_path);
  if (name === 'edit' && text(args.old_string) && typeof args.new_string === 'string' && args.old_string !== args.new_string &&
    (args.replace_all === undefined || typeof args.replace_all === 'boolean')) return text(args.file_path);
  if (name !== 'str_replace_editor') return null;
  const valid = args.command === 'create' && typeof args.file_text === 'string' ||
    args.command === 'str_replace' && text(args.old_str) && (args.new_str === undefined || typeof args.new_str === 'string') ||
    args.command === 'insert' && Number.isInteger(args.insert_line) && args.insert_line >= 0 && typeof args.new_str === 'string';
  return valid ? text(args.path) : null;
}

function receiptOf(message) {
  for (const block of message.content || []) {
    if (block.type !== 'text') continue;
    const parsed = argumentsOf(block.text);
    if (parsed) return parsed;
  }
  return null;
}

function fileReferences(receipt) {
  const files = [];
  // Only explicit output references count. Input paths and prose never establish production.
  const visit = (value, {stage = null, step = ''} = {}, depth = 0) => {
    if (depth > 5) return;
    if (Array.isArray(value)) { for (const child of value) visit(child, {stage, step}, depth + 1); return; }
    if (!object(value)) return;
    for (const [key, child] of Object.entries(value)) {
      const field = OUTPUT_FILES.get(key);
      if (field) {
        const [defaultStage, defaultTitle] = field;
        const title = step === 'questions' && ['report', 'report_path', 'quality_report'].includes(key)
          ? '题目质量报告' : defaultTitle;
        for (const file of Array.isArray(child) ? child : [child]) {
          if (text(file)) files.push({path: file, title, stage: stage || defaultStage});
        }
      } else if (key === 'artifacts' && Array.isArray(child)) {
        for (const row of child) {
          if (text(row)) files.push({path: row, title: '', stage});
          else if (object(row) && text(row.path)) files.push({path: row.path, title: text(row.title) || '',
            stage: Stage.safeParse(row.stage).success ? row.stage : stage});
        }
      } else if (key === 'steps' && object(child)) {
        for (const [stepName, record] of Object.entries(child)) {
          if (!object(record)) continue;
          for (const output of ['result', 'artifact', 'outputs']) {
            visit(record[output], {stage: STEP_STAGES[stepName] || null, step: stepName}, depth + 1);
          }
        }
      } else if (key === 'tasks' && (object(child) || Array.isArray(child))) {
        for (const task of Array.isArray(child) ? child : Object.values(child)) visit(task, {stage, step}, depth + 1);
      } else if (OUTPUT_CONTAINERS.has(key)) {
        visit(child, {stage, step}, depth + 1);
      }
    }
  };
  visit(receipt);
  return files;
}

function publish(state, {files = [], workspace, captureId}, seq) {
  let view = state.view;
  const nextFiles = [...view.files];
  let changed = false;
  for (const file of files) {
    const absolute = resolve(workspace || state.cwd, file.path);
    if (!absolute) continue;
    const entry = {...file, path: absolute, seq};
    const previous = nextFiles.findIndex((row) => row.path === absolute);
    if (previous < 0) nextFiles.push(entry);
    else nextFiles[previous] = entry;
    changed = true;
  }
  if (nextFiles.length > 500) nextFiles.splice(0, nextFiles.length - 500);
  const workspaces = workspace && !view.workspaces.includes(workspace) ? [...view.workspaces, workspace] : view.workspaces;
  const captureIds = captureId && !view.captureIds.includes(captureId) ? [...view.captureIds, captureId] : view.captureIds;
  const primaryWorkspace = workspace || view.primaryWorkspace;
  if (changed || workspaces !== view.workspaces || captureIds !== view.captureIds || primaryWorkspace !== view.primaryWorkspace) {
    view = {revision: seq, workspaces, primaryWorkspace, files: changed ? nextFiles : view.files, captureIds};
  }
  return view === state.view ? state : {...state, view};
}

/** A framework-owned, replayable fold. Pending calls never enter the wire view. */
export const artifactProjection = {
  key: ARTIFACT_PROJECTION, stateVersion: 3, stateSchema: State,
  init: (header) => ({cwd: header.cwd || '', calls: {}, view: {
    revision: 0, workspaces: [], primaryWorkspace: null, files: [], captureIds: [],
  }}),
  apply(state, event) {
    if (event.type === 'deliverables/presented') {
      const files = (event.data.files || []).filter((row) => object(row) && text(row.path)).map((row) => ({
        path: row.path, title: text(row.description)?.slice(0, 160) || '', stage: null, source: 'present',
      }));
      return publish(state, {files}, event.seq);
    }
    if (event.type === 'tool/call') {
      const {name, callId} = event.data;
      if (!name.startsWith('benchforge_') && !['write', 'edit', 'str_replace_editor'].includes(name)) return state;
      const args = argumentsOf(event.data.arguments);
      if (!args) return state;
      const file = mutationFile(name, args);
      if (!name.startsWith('benchforge_') && !file) return state;
      return {...state, calls: {...state.calls, [callId]: {name, workspace: text(args.workspace) || state.cwd, file}}};
    }
    if (event.type !== 'tool/result' || event.surfaceOp === 'replace') return state;
    const {message, meta, error} = event.data;
    const callId = message?.source?.callId || message?.toolCallId;
    const call = state.calls[callId];
    if (!call) return state;
    const calls = {...state.calls}; delete calls[callId];
    const settled = {...state, calls};
    if (message.isError || error) return settled;
    if (call.file) {
      // A recorded absolute target wins; malformed metadata must not turn a relative path into an authorization.
      const recorded = meta && Object.hasOwn(meta, 'path');
      const file = recorded ? text(meta.path) : call.file;
      if (!file || recorded && !filesystem(state.cwd).isAbsolute(file)) return settled;
      return publish(settled, {files: [{path: file, title: '', stage: null, source: 'mutation'}]}, event.seq);
    }
    const receipt = receiptOf(message);
    if (!receipt || receipt.error || ['failed', 'error', 'rejected'].includes(String(receipt.state || receipt.status).toLowerCase())) return settled;
    const files = fileReferences(receipt).map((file) => ({...file, source: 'tool'}));
    const captureId = /^sf_[A-Za-z0-9_-]+$/.test(receipt.run_id || '') ? receipt.run_id : null;
    const workspace = text(receipt.artifact_dir) || files.length || captureId ? resolve(state.cwd, call.workspace) : null;
    return publish(settled, {files, workspace, captureId}, event.seq);
  },
  wire: {viewSchema: ArtifactView, view: (state) => state.view, summary: false},
};
