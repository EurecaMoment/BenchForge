import fs from 'node:fs/promises';
import path from 'node:path';
import {randomUUID} from 'node:crypto';
import {readChildCatalog} from '../../host/session_family.mjs';

const queues = new Map();
const nativeIdleProbes = new WeakMap();
const NATIVE_ACTIVITY_PROBE_MS = 50;
const SCHEMA = 'benchforge.synthesis-sessions/v1';
const PERSONA = `You are a BenchForge preparation worker for one self-contained artifact assignment. Read the supplied frozen plan, contracts and relevant saved evidence. Write only inside the assigned output directory. Start file operations by changing to that directory and using explicit paths; preserve parent plans, scene selections, runner state, accepted artifacts and other workers' files.
At the start of file work, batch-copy the supplied source scripts or programs into your directory and save manifest.json with status preparing, the working artifact paths and known missing inputs. Use the supplied source paths instead of rediscovering the workspace or checking the whole production chain first. On recovery, preserve saved edits and continue the existing working copies. If a source is actually missing, record its exact path and continue the already determined scope; when no source was supplied, save the known design or script scaffold and preparing manifest first.
Apply the smallest concrete patch. Locate and batch-read only the functions or sections needed for it, then save known changes as you work. For scene design, persist the title, descriptive summary and program draft before investigating unresolved structure. If an exact input or required structure remains unknown, state the specific question or missing field in the manifest and hand off the completed scope; do not delay all saved preparation while trying to validate the entire production pipeline.
Keep verification proportional to the actual change and unresolved evidence. Do not add complex SHA checks or compute checksums for every input, PNG, output and receipt. Preserve supplied native identities and use necessary file size/mtime version bindings. Serialize JSON/JSONL directly with explicit UTF-8 writes; do not re-read every newly written record just to check serialization. Do not repeatedly scan directories, poll unchanged state or revalidate unrelated completed stages. Run only the checks needed for the patch and its source/production boundary; broaden checks when a concrete failure requires it.
Preparation is complete when the requested edits or design artifacts are saved, checks proportional to those changes have run, and an honest manifest reports artifact paths, actual checks, missing inputs and unresolved issues. A useful saved partial preparation can be handed off with its concrete blocker. Parent production acceptance is a later responsibility and must not prevent preparation drafts from being saved or delivered. Preserve hard requirements, exact measurement and public-question contracts, provenance, physics and everyday realism. Recognizable real assets are the default; geometry explicitly declared in the frozen plan remains valid in its scope. Predictions and references are not captured evidence or GT; record unaccepted or missing inputs without inventing results.
For scene facing and functional-layout work, use only the calibrated asset-front evidence supplied in granted inputs or the public catalog. Do not inspect deployment or third-party source files. Do not infer an asset's front from AABB dimensions or an earlier visual-review pass. Unknown assets have no implied axis. Face monitors toward their operator, chairs toward their working target and conversation seats toward their conversation target; preserve uncertainty until native capture verifies it.
The parent owns authoritative merging, capture, acceptance, export and each production stage's user window. Do not spawn more workers, create stage windows or bypass parent-owned tools through a shell, HTTP or task code. Question workers deliver S4 preparation or shards; the parent packages S5 after aggregate S4 acceptance. Update manifest.json to reflect the actual preparation outcome and quote its path in the final handoff. Preparation completion and accepted scene/data production are separate outcomes.`;

class SynthesisError extends Error {
  /** 创建带响应状态的准备子任务错误。 */
  constructor(message, status = 400) { super(message); this.status = status; }
}
/** 判断准备文件与记录目录是否位于受管工作区之内。 */
const inside = (root, value) => {
  const relative = path.relative(root, value);
  return relative === '' || relative !== '..' && !relative.startsWith('..' + path.sep) && !path.isAbsolute(relative);
};
/** 校验必需字段为非空文本，并在错误中标明字段名。 */
const nonempty = (value, field) => {
  if (typeof value !== 'string' || !value.trim()) throw new SynthesisError(`${field} must be non-empty text`);
  return value;
};
/** 验证请求为对象且没有未支持字段，避免接受含糊的委派参数。 */
function fields(value, allowed, name) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new SynthesisError(`${name} must be an object`);
  const extra = Object.keys(value).filter(key => !allowed.includes(key));
  if (extra.length) throw new SynthesisError(`Unsupported ${name} fields: ${extra.join(', ')}`);
}
/** 读取批次 JSON 记录，尚未创建时返回空值。 */
async function json(file) {
  try { return JSON.parse(await fs.readFile(file, 'utf8')); }
  catch (error) { if (error.code === 'ENOENT') return null; throw error; }
}
/** 通过唯一临时文件原子替换批次记录，并清理遗留临时文件。 */
async function write(file, value) {
  const temporary = file + '.' + randomUUID() + '.tmp';
  try { await fs.writeFile(temporary, JSON.stringify(value, null, 2)); await fs.rename(temporary, file); }
  finally { await fs.unlink(temporary).catch(error => { if (error.code !== 'ENOENT') throw error; }); }
}
/** 解析真实目录并校验其存在且类型正确。 */
async function directory(value, name) {
  let resolved;
  try { resolved = await fs.realpath(value); }
  catch (error) {
    if (error.code === 'ENOENT') throw new SynthesisError(`${name} must be an existing directory`);
    throw error;
  }
  if (!(await fs.stat(resolved)).isDirectory()) throw new SynthesisError(`${name} must be a directory`);
  return resolved;
}
/** 解析真实普通文件，缺失或类型不符时提供字段相关错误。 */
async function existingFile(value, name) {
  let resolved;
  try { resolved = await fs.realpath(value); }
  catch (error) {
    if (error.code === 'ENOENT') throw new SynthesisError(`${name} must be an existing file`);
    throw error;
  }
  if (!(await fs.stat(resolved)).isFile()) throw new SynthesisError(`${name} must be a file`);
  return resolved;
}
/** 逐级创建准备任务记录目录，并确保每一级仍位于受管工作区内。 */
async function recordDirectory(root) {
  let current = root;
  for (const name of ['.benchforge', 'synthesis-sessions']) {
    const next = path.join(current, name);
    try { await fs.mkdir(next); } catch (error) { if (error.code !== 'EEXIST') throw error; }
    current = await directory(next, 'synthesis record directory');
    if (!inside(root, current)) throw new SynthesisError('Synthesis records must stay inside the workspace', 403);
  }
  return current;
}
/** 将批次任务状态汇总为接受、失败和等待数量，并生成去重的可读错误回执。 */
function receipt(record, recordPath, reason) {
  const acceptedCount = record.tasks.filter(task => task.state === 'accepted').length;
  const failedCount = record.tasks.filter(task => task.state === 'failed').length;
  const pending = record.tasks.some(task => ['pending', 'starting'].includes(task.state));
  const errors = [...new Set(record.tasks.filter(task => task.error).map(task =>
    task.error.split('; known global tools:')[0]))];
  if (errors.length) reason = 'Preparation startup failed or is unconfirmed: ' + errors.join('; ');
  return {parentId: record.parentId, batchKey: record.batchKey, recordPath,
    ...(record.planPath ? {planPath: record.planPath} : {}),
    state: pending ? 'waiting' : failedCount ? acceptedCount ? 'partial_failure' : 'failed' : 'accepted',
    acceptedCount, failedCount, ...(reason ? {reason} : {}),
    ...(record.reasoningSelection ? {reasoningSelection: record.reasoningSelection} : {}),
    tasks: record.tasks.map(({prompt, ...task}) => task)};
}
/** 按记录键串行执行批次变更，前次失败不阻断后续请求。 */
async function serialized(key, operation) {
  const previous = queues.get(key) || Promise.resolve();
  const current = previous.catch(() => {}).then(operation);
  queues.set(key, current);
  try { return await current; }
  finally { if (queues.get(key) === current) queues.delete(key); }
}
/** 以排他文件锁持有批次操作权，并提供关闭和删除锁文件的清理函数。 */
async function lock(file) {
  let opened;
  try {
    const handle = opened = await fs.open(file, 'wx');
    await handle.writeFile(JSON.stringify({pid: process.pid}));
    return async () => { await handle.close(); await fs.unlink(file).catch(error => { if (error.code !== 'ENOENT') throw error; }); };
  } catch (error) {
    if (opened) {
      await opened.close();
      await fs.unlink(file).catch(remove => { if (remove.code !== 'ENOENT') throw remove; });
    }
    if (error.code !== 'EEXIST') throw error;
    const owner = await json(file);
    if (Number.isSafeInteger(owner?.pid)) {
      try { process.kill(owner.pid, 0); }
      catch (probe) {
        if (probe.code === 'ESRCH') {
          await fs.unlink(file).catch(remove => { if (remove.code !== 'ENOENT') throw remove; });
          return lock(file);
        }
      }
    }
    return null;
  }
}

/** 提取原生 Agent 活动检查异常的名称、消息和可选错误码。 */
function activityError(error) {
  return {name: error?.name || 'Error', message: error?.message || String(error),
    ...(error?.code !== undefined ? {code: error.code} : {})};
}

/** 对 Agent 的原生空闲等待建立有界读取探针，超时后读取请求不继续无限阻塞。 */
function nativeIdleProbe(agent) {
  const cached = nativeIdleProbes.get(agent);
  if (cached?.state === 'pending') return cached;
  const probe = {state: 'pending'};
  let finishRead;
  let timer;
  // Reads await this bounded promise, never the Agent's unbounded promise.
  // Once timed out, subsequent reads of a pending probe return immediately.
  probe.readReady = new Promise(resolve => {
    finishRead = /** 清除空闲探针的读取超时并释放当前有界等待。 */ () => { clearTimeout(timer); resolve(); };
    timer = setTimeout(resolve, NATIVE_ACTIVITY_PROBE_MS);
  });
  nativeIdleProbes.set(agent, probe);
  try {
    probe.idlePromise = agent.whenIdle();
    if (!probe.idlePromise || typeof probe.idlePromise.then !== 'function') {
      probe.state = 'unknown';
      probe.reason = 'Agent.whenIdle() did not return a promise';
      finishRead();
    } else {
      // Exactly one completion listener for an unfinished Agent probe. Readers
      // reuse readReady instead of racing or reattaching to idlePromise.
      Promise.resolve(probe.idlePromise).then(() => {
        probe.state = 'settled'; finishRead();
      }, error => {
        probe.state = 'error'; probe.error = activityError(error); finishRead();
      });
    }
  } catch (error) {
    probe.state = 'error'; probe.error = activityError(error); finishRead();
  }
  return probe;
}

/**
 * A trusted server adapter for native continuable spawn. The caller owns route
 * authentication and registered-workspace admission. The Host reserves each
 * child's independent workspace and hands off explicit input snapshots.
 */
/** 创建受父会话计划约束的准备子任务批次接口，限制并发并持久化原生子会话归属。 */
export function createSynthesisSessions({ctx, workspacePath, batchSize = 3}) {
  if (!Number.isSafeInteger(batchSize) || batchSize < 1) throw new SynthesisError('batchSize must be a positive integer');
  /** 从当前宿主上下文获取可用原生服务。 */
  const get = name => ctx.get(name);

  /** 读取原生子 Agent 的活动与空闲确认状态，用有界探针避免状态查询挂起。 */
  async function nativeActivity(agentId) {
    let agent;
    try {
      agent = get('agents')?.get(agentId);
      if (!agent) return {available: false, agentStatus: null, idleConfirmed: false, activityPending: null};
      const probe = typeof agent.whenIdle === 'function' ? nativeIdleProbe(agent) : undefined;
      if (probe) await probe.readReady;
      const agentStatus = typeof agent.status === 'string' ? agent.status : null;
      if (!probe || probe.state === 'unknown') return {available: true, agentStatus,
        idleConfirmed: false, activityPending: agentStatus === 'running' ? true : null,
        reason: probe?.reason || 'Agent.whenIdle() is unavailable; agentStatus alone cannot confirm idle'};
      if (probe.state === 'error') return {available: true, agentStatus, idleConfirmed: false,
        activityPending: agentStatus === 'running' ? true : null, error: probe.error};
      return {available: true, agentStatus,
        idleConfirmed: probe.state === 'settled' && agentStatus === 'idle',
        activityPending: probe.state === 'pending' || agentStatus === 'running'};
    } catch (error) {
      return {available: Boolean(agent), agentStatus: null, idleConfirmed: false,
        activityPending: null, error: activityError(error)};
    }
  }

  /** 从父会话当前模型注册表确认可用于准备子任务的推理强度。 */
  async function preparationReasoning(parent, signal) {
    const llm = parent.ctx?.get('llm') || get('llm');
    if (typeof llm?.resolveModelInfo !== 'function') throw new SynthesisError('The parent model capability registry is unavailable', 503);
    // The native delegation resolver inherits the latest request route, then
    // falls back to creation options before the parent's first request.
    const route = parent.session.requestHeader?.()?.config || parent.options;
    const provider = nonempty(route?.provider, 'parent provider'), model = nonempty(route?.model, 'parent model');
    const info = await llm.resolveModelInfo(provider, model, signal);
    const supportedEfforts = info.reasoning?.efforts?.map(effort => effort.id) || [];
    const preferredEffort = 'low';
    const selectedEffort = ['low', 'minimal', 'none', 'off', 'medium', 'high', 'xhigh']
      .find(effort => supportedEfforts.includes(effort)) || supportedEfforts[0];
    const selectionMode = selectedEffort ? 'explicit' : info.reasoning ? 'inherit' : 'not_applicable';
    return {provider, model, preferredEffort, selectionMode,
      ...(selectedEffort ? {selectedEffort} : {}), supportedEfforts,
      ...(info.reasoning?.defaultEffort ? {providerDefaultEffort: info.reasoning.defaultEffort} : {}),
      reason: !selectedEffort ? 'The model exposes no selectable reasoning effort; no reasoningEffort override is supplied.' :
        selectedEffort === preferredEffort ? 'The model supports the preferred preparation effort.' :
          !['minimal', 'none', 'off', 'medium', 'high', 'xhigh'].includes(selectedEffort)
            ? `The model exposes no supported preferred or lighter preparation level; its advertised ${selectedEffort} is selected explicitly.`
            : `The model does not support ${preferredEffort}; preparation uses supported ${selectedEffort}. Provider/model and parent effort are unchanged.`};
  }

  /** 校验父会话、工作区和所需原生服务，建立批次操作的可信作用域。 */
  async function scope(parentId, requireAgent = true, signal) {
    nonempty(parentId, 'parentId');
    const parent = get('agents')?.get(parentId);
    if (requireAgent && (!parent || parent.id !== parentId || parent.session?.header?.id !== parentId)) {
      throw new SynthesisError('The current parent Agent is unavailable', 409);
    }
    const root = await directory(workspacePath, 'workspace');
    const parentHeader = parent?.session?.header || (await header(parentId, signal)).header;
    const cwd = await directory(nonempty(parentHeader?.cwd, 'parent cwd'), 'parent cwd');
    if (!inside(root, cwd)) throw new SynthesisError('The parent Agent belongs to another workspace', 403);
    const service = get('subagents');
    if (!service?.startActivation) throw new SynthesisError('Native subagent service is unavailable', 503);
    const access = get('benchforgeAccess');
    if (!access) throw new SynthesisError('Managed conversation workspaces are unavailable', 503);
    if (access.get(parentId).rootPath !== cwd || root !== cwd)
      throw new SynthesisError('Preparation requires the parent managed workspace', 403);
    return {root, parent, service, access, dir: await recordDirectory(root)};
  }
  /** 将非空批次键编码为工作区记录目录下的安全文件名。 */
  const fileOf = (dir, batchKey) => path.join(dir, 'batch-' + Buffer.from(nonempty(batchKey, 'batchKey'), 'utf8').toString('base64url') + '.json');

  /** 优先读取驻留子会话头，否则通过官方观察接口确认身份并释放快照。 */
  async function header(childId, signal) {
    signal?.throwIfAborted();
    const live = get('sessions')?.get(childId);
    if (live) return {header: live.header, absent: false};
    const query = get('sessionQuery');
    if (!query?.observeSession) return {absent: false};
    try {
      const observed = await query.observeSession(childId, {signal, projectionMode: 'none'});
      try { return {header: observed.header, absent: false}; }
      finally { observed[Symbol.dispose](); }
    } catch (error) {
      if (signal?.aborted) throw error;
      return {absent: ['SESSION_QUERY_SESSION_NOT_FOUND', 'session/not-found'].includes(error.code)};
    }
  }
  /** 使用原生子 Agent 目录和会话头协调启动不确定或失败的任务，避免重复创建子会话。 */
  async function reconcile(record, signal) {
    const uncertain = record.tasks.filter(task => ['starting', 'failed'].includes(task.state));
    if (!uncertain.length) return false;
    let changed = false;
    const catalog = await readChildCatalog(get('sessionQuery'), record.parentId, signal);
    for (const task of uncertain) {
      const entry = catalog.find(child => child.id === task.childId);
      const observed = await header(task.childId, signal);
      if (observed.header && (observed.header.parentSession !== record.parentId || observed.header.origin !== 'subagent')) {
        throw new SynthesisError('Reserved child identity belongs to another parent', 409);
      }
      // Native catalog commit occurs only after initial inbox acceptance.
      if (entry?.mode === 'continuable' && observed.header) {
        task.state = 'accepted'; task.recovered = true; delete task.error;
        changed = true;
      } else if (task.state === 'starting' && observed.absent) {
        task.state = 'pending';
        changed = true;
      }
    }
    return changed;
  }
  /** 统计工作区全部批次中仍在运行或启动中的原生子任务。 */
  async function occupied(dir) {
    let active = 0;
    for (const name of (await fs.readdir(dir)).filter(name => name.startsWith('batch-') && name.endsWith('.json'))) {
      const record = await json(path.join(dir, name));
      if (record?.schema !== SCHEMA) continue;
      for (const task of record.tasks) {
        if (get('agents')?.get(task.childId)?.status === 'running' || task.state === 'starting') active++;
      }
    }
    return active;
  }

  /** 验证父计划与任务清单，在互斥和并发预算内幂等创建原生准备子会话。 */
  async function start(request, signal) {
    fields(request, ['parentId', 'batchKey', 'tasks', 'planPath'], 'synthesis request');
    const {root, parent, service, access, dir} = await scope(request.parentId, true, signal);
    const batchKey = nonempty(request.batchKey, 'batchKey');
    const planPath = request.planPath === undefined ? undefined : await existingFile(
      path.resolve(root, nonempty(request.planPath, 'planPath')), 'planPath');
    if (planPath && !inside(root, planPath)) throw new SynthesisError('The saved plan must stay inside the workspace', 403);
    if (!Array.isArray(request.tasks) || !request.tasks.length) throw new SynthesisError('tasks must contain preparation work');
    const tasks = await Promise.all(request.tasks.map(/** 校验每个子任务字段、输出目录和输入文件，建立明确且独立的委派身份。 */ async task => {
      fields(task, ['key', 'label', 'prompt', 'outputDirectory', 'inputFiles'], 'synthesis task');
      const outputDirectory = await directory(path.resolve(root, nonempty(task.outputDirectory, 'outputDirectory')), 'outputDirectory');
      if (outputDirectory === root || !inside(root, outputDirectory)) throw new SynthesisError('Worker output must be a dedicated directory inside the workspace', 403);
      if (!/^(tasks|synthesis)[\\/]/.test(path.relative(root, outputDirectory)))
        throw new SynthesisError('Preparation handoffs must stay in parent draft directories', 403);
      if (task.inputFiles !== undefined && (!Array.isArray(task.inputFiles) || task.inputFiles.length > 32))
        throw new SynthesisError('inputFiles must contain at most 32 explicit parent files');
      const inputFiles = await Promise.all((task.inputFiles || []).map(async value => {
        const file = await existingFile(path.resolve(root, nonempty(value, 'input file')), 'input file');
        if (!inside(root, file)) throw new SynthesisError('Preparation input leaves the parent workspace', 403);
        return file;
      }));
      return {key: nonempty(task.key, 'key'), label: nonempty(task.label, 'label'),
        prompt: nonempty(task.prompt, 'prompt'), outputDirectory, ...(inputFiles.length ? {inputFiles} : {})};
    }));
    tasks.sort((a, b) => a.key < b.key ? -1 : a.key > b.key ? 1 : 0);
    for (let i = 0; i < tasks.length; i++) for (let j = i + 1; j < tasks.length; j++) {
      if (tasks[i].key === tasks[j].key || inside(tasks[i].outputDirectory, tasks[j].outputDirectory) || inside(tasks[j].outputDirectory, tasks[i].outputDirectory)) {
        throw new SynthesisError('Preparation tasks require distinct keys and non-overlapping output directories');
      }
    }
    const recordPath = fileOf(dir, batchKey);
    const identity = {parentId: parent.id, batchKey, workspacePath: root, ...(planPath ? {planPath} : {}), tasks};
    return serialized(dir, /** 在批次文件锁内核验幂等身份和并发预算，持久化启动状态后创建原生子任务。 */ async () => {
      const release = await lock(path.join(dir, '.startup.lock'));
      if (!release) {
        const saved = await json(recordPath);
        if (saved) {
          if (saved.schema !== SCHEMA || JSON.stringify(saved.identity) !== JSON.stringify(identity)) {
            throw new SynthesisError('This batchKey already binds different preparation work; use a new batchKey for changes', 409);
          }
          return receipt(saved, recordPath, 'Another workspace preparation batch is being registered');
        }
        return {parentId: parent.id, batchKey, recordPath, state: 'waiting', acceptedCount: 0, failedCount: 0,
          tasks: [], reason: 'Another workspace preparation batch is being registered'};
      }
      try {
        let record = await json(recordPath);
        if (record && (record.schema !== SCHEMA || JSON.stringify(record.identity) !== JSON.stringify(identity))) {
          throw new SynthesisError('This batchKey already binds different preparation work; use a new batchKey for changes', 409);
        }
        if (!record) {
          record = {schema: SCHEMA, identity, parentId: parent.id, batchKey, workspacePath: root,
            ...(planPath ? {planPath} : {}),
            createdAt: Date.now(), tasks: tasks.map(task => ({...task, childId: randomUUID(), state: 'pending'}))};
          await write(recordPath, record);
        }
        await reconcile(record, signal);
        await write(recordPath, record);
        const active = await occupied(dir);
        const slots = Math.max(0, batchSize - active);
        const ready = record.tasks.filter(task => ['pending', 'failed'].includes(task.state)).slice(0, slots);
        if (!ready.length) {
          await write(recordPath, record);
          return receipt(record, recordPath, record.tasks.some(task => task.state !== 'accepted')
            ? active >= batchSize ? `${active} preparation tasks occupy this workspace's batch budget (${batchSize})`
              : 'A reserved child exists but initial inbox acceptance is not yet confirmed' : undefined);
        }
        const reasoningSelection = await preparationReasoning(parent, signal);
        record.reasoningSelection = reasoningSelection;
        for (const task of ready) {
          task.state = 'starting'; task.reasoningSelection = reasoningSelection; delete task.error;
        }
        await write(recordPath, record); // Reserve durable identities before native creation.
        let saves = Promise.resolve();
        /** 将当前批次快照串行写入记录文件，保持任务启动状态的顺序。 */
        const save = () => { saves = saves.then(() => write(recordPath, record)); return saves; };
        await Promise.all(ready.map(/** 为待启动子任务预留独立工作区、制作输入快照并调用原生激活接口。 */ async task => {
          try {
            signal?.throwIfAborted();
            const binding = await access.reserve(task.childId, parent.id);
            const childOutputDirectory = path.join(binding.rootPath, 'tasks', 'preparation');
            await fs.mkdir(childOutputDirectory, {recursive: true});
            task.childWorkspace = binding.rootPath;
            task.childOutputDirectory = childOutputDirectory;
            const inputs = [...new Set([...(planPath ? [planPath] : []), ...(task.inputFiles || [])])];
            task.inputSnapshots = await Promise.all(inputs.map(async source => ({
              source, path: await access.snapshotInput(task.childId, parent.id, source),
            })));
            const frozenPlan = task.inputSnapshots.find(input => input.source === planPath)?.path;
            await save();
            const result = await service.startActivation({provider: 'spawn', childId: task.childId, label: task.label,
              parent, cwd: binding.rootPath, delivery: 'parent', prompt: [{type: 'text', text:
                PERSONA + '\n\n' +
                (frozenPlan ? `Saved frozen plan: ${frozenPlan}\nRead this snapshot for the assigned preparation.\n\n` : '') +
                `Granted input snapshots: ${JSON.stringify(task.inputSnapshots)}\nUse snapshot paths instead of their original parent paths.\n` +
                `Assigned output directory: ${childOutputDirectory}\nSave preparation files directly in this directory. The Host hands them back when you finish; do not write the parent workspace.\n\n${task.prompt}`}],
              ...(reasoningSelection.selectedEffort ? {agentOptions: {reasoningEffort: reasoningSelection.selectedEffort}} : {}),
              maxDepth: 1, signal: signal || new AbortController().signal});
            if (result.childId !== task.childId || typeof result.messageId !== 'string' || !result.messageId) {
              throw new Error('Native startup returned an incomplete identity receipt');
            }
            task.state = 'accepted'; task.messageId = result.messageId;
          } catch (error) {
            task.error = error.message || String(error);
            // A typed native rejection is a failed attempt; uncertain startup
            // retains the reserved id for catalog/header reconciliation.
            task.state = error.name === 'SubagentError' && error.code !== 'DUPLICATE_CHILD' && !signal?.aborted ? 'failed' : 'starting';
          }
          await save();
        }));
        await reconcile(record, signal?.aborted ? undefined : signal);
        await save();
        return receipt(record, recordPath, record.tasks.some(task => task.state === 'pending')
          ? 'Remaining preparation work is waiting for the workspace batch budget' : undefined);
      } finally { await release(); }
    });
  }
  /** 读取并协调指定父会话的批次记录，补充原生活动状态后返回当前回执。 */
  async function read({parentId, batchKey}, signal) {
    const {dir} = await scope(parentId, false, signal);
    const recordPath = fileOf(dir, batchKey);
    const value = await serialized(dir, /** 串行读取并协调批次记录，验证父会话归属后补充原生活动状态。 */ async () => {
      const record = await json(recordPath);
      if (!record) return {parentId, batchKey, recordPath, state: 'not_started', acceptedCount: 0, tasks: []};
      if (record.parentId !== parentId) throw new SynthesisError('This batch belongs to another parent', 403);
      const release = await lock(path.join(dir, '.startup.lock'));
      if (!release) return receipt(record, recordPath, 'Workspace preparation registration is in progress');
      try {
        if (await reconcile(record, signal)) await write(recordPath, record);
        return receipt(record, recordPath);
      } finally { await release(); }
    });
    const [parentNativeActivity, ...taskActivities] = await Promise.all([
      nativeActivity(parentId), ...value.tasks.map(task => nativeActivity(task.childId)),
    ]);
    const access = get('benchforgeAccess');
    for (let index = 0; index < value.tasks.length; index++) {
      const task = value.tasks[index];
      if (access && task.childOutputDirectory && taskActivities[index].idleConfirmed) {
        try { task.handoff = await access.exportPreparation(task.childId, parentId, task.outputDirectory); }
        catch (error) { task.handoff = {accepted: false, error: error.message}; }
      }
    }
    return {...value, parentNativeActivity,
      tasks: value.tasks.map((task, index) => ({...task, nativeActivity: taskActivities[index]}))};
  }
  return {start, read};
}
