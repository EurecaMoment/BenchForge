import fs from 'node:fs/promises';
import path from 'node:path';
import {randomUUID} from 'node:crypto';
import {AccessError, within} from './workspaces.mjs';

/** 构造带 HTTP 状态的工作区迁移访问错误。 */
const fail = (message, status = 409) => Object.assign(new AccessError(message), {status});
/** 优先读取观察结果的正式版本，没有版本时以事件游标作为一致性标记。 */
const revisionOf = observation => observation.revision ?? {cursor: observation.cursor};
/** 比较可序列化的迁移版本或清单是否完全一致。 */
const equal = (left, right) => JSON.stringify(left) === JSON.stringify(right);
/** 将文件相对路径、类型与版本编码为迁移候选标识，使文件变化能够被检测。 */
const candidateId = file => Buffer.from(JSON.stringify([
  file.relativePath, file.kind, file.version.size, file.version.mtimeMs,
])).toString('base64url');

/** Host-only administration. Selections are looked up in fresh native/PG evidence. */
/** 创建迁移预览和执行接口；只有用户选择且版本仍一致的源会话文件可以迁入新工作区。 */
export function createWorkspaceMigration({observe, readOperations, access, sessions, agents, createMessage,
  projectionKey = 'benchforgeArtifacts'}) {
  /** 检查源会话已停止且具有原生产物投影，返回可迁移文件及当前源版本。 */
  async function preview(sessionId, signal) {
    if (typeof sessionId !== 'string' || !/^session-[A-Za-z0-9-]+$/.test(sessionId))
      throw fail('A source conversation ID is required', 400);
    if (agents.get(sessionId)?.status === 'running') throw fail('Stop the source conversation before migration');
    const observation = await observe(sessionId, {signal});
    try {
      const {header} = observation, view = observation.projections?.values[projectionKey];
      if (header.id !== sessionId || header.agentPreset !== 'benchforge' || !header.cwd || !view)
        throw fail('A BenchForge conversation with its native artifact projection is required');
      const root = await fs.realpath(header.cwd);
      const declaredRoot = path.resolve(header.cwd);
      // Rebase the trusted root alias, preserving every task-controlled child
      // component so the regular-file/link check below still rejects links.
      /** 把声明根目录中的文件路径映射到经过解析的真实源工作区。 */
      const resolveSource = value => {
        const file = path.resolve(root, value);
        return within(declaredRoot, file) ? path.resolve(root, path.relative(declaredRoot, file)) : file;
      };
      const operations = await readOperations(root, sessionId, signal);
      const roots = operations.filter(row => row.session_id === sessionId && row.state === 'completed')
        .map(row => resolveSource(row.artifact))
        .filter(directory => within(path.join(root, 'operations'), directory) && directory !== path.join(root, 'operations'));
      const references = [...view.files, ...roots.flatMap(directory => ['request.json', 'result.json']
        .map(name => ({path: path.join(directory, name), source: 'tool'})))];
      const files = new Map(), omitted = [];
      for (const reference of references) {
        signal?.throwIfAborted();
        const file = resolveSource(reference.path), relativePath = path.relative(root, file).split(path.sep).join('/');
        const owned = roots.some(directory => within(directory, file));
        const kind = owned ? 'artifact' : reference.source === 'mutation' && relativePath.startsWith('tasks/') ? 'draft' : null;
        if (!kind || !within(root, file)) { omitted.push({path: reference.path, reason: 'ownership_unconfirmed'}); continue; }
        try {
          const actual = await fs.realpath(file), stat = await fs.lstat(file);
          if (actual !== file || !stat.isFile()) { omitted.push({path: reference.path, reason: 'not_regular_file'}); continue; }
          const row = {relativePath, kind, version: {size: stat.size, mtimeMs: stat.mtimeMs}};
          files.set(relativePath, {...row, id: candidateId(row)});
        } catch (error) {
          if (!['ENOENT', 'ENOTDIR'].includes(error.code)) throw error;
          omitted.push({path: reference.path, reason: 'missing'});
        }
      }
      const candidates = [...files.values()].sort((a, b) => a.relativePath.localeCompare(b.relativePath));
      return {session_id: sessionId, source_revision: revisionOf(observation), candidates, omitted,
        truncated_operations: operations.length === 201, history_preserved: true, production_authority_transferred: false};
    } finally { observation[Symbol.dispose](); }
  }

  /** 验证显式候选选择和源版本，在维护作用域内创建并完成独立会话迁移。 */
  async function migrate(sessionId, selection, signal) {
    if (!selection || typeof selection !== 'object' || Array.isArray(selection) ||
        Object.keys(selection).some(key => !['source_revision', 'selected'].includes(key)) ||
        !Array.isArray(selection.selected) || selection.selected.length > 902 ||
        selection.selected.some(id => typeof id !== 'string') ||
        new Set(selection.selected).size !== selection.selected.length || selection.source_revision === undefined)
      throw fail('Select candidate IDs and the source revision returned by migration preview', 400);
    /** 合并请求与维护取消信号，重新核验候选文件后创建目标会话并复制批准的输入。 */
    const operation = async maintenanceSignal => {
      const combined = signal && maintenanceSignal ? AbortSignal.any([signal, maintenanceSignal]) : signal || maintenanceSignal;
      const current = await preview(sessionId, combined);
      if (!equal(current.source_revision, selection.source_revision)) throw fail('Source conversation changed; refresh migration preview');
      const available = new Map(current.candidates.map(row => [row.id, row]));
      const files = selection.selected.map(id => {
        const row = available.get(id);
        if (!row) throw fail('A selected file changed or is no longer owned; refresh migration preview');
        return row;
      });
      const newSessionId = 'session-' + randomUUID();
      const prepared = await access.prepareMigration({sourceSessionId: sessionId, newSessionId,
        sourceRevision: current.source_revision, files}, combined);
      combined?.throwIfAborted();
      // The normal controller owns preset composition, header creation and workspace attachment.
      const created = await sessions.create({sessionId: newSessionId, cwd: prepared.rootPath, agentPreset: 'benchforge'});
      const agent = agents.get(created.sessionId);
      if (!agent) throw fail('Migrated conversation was created but is unavailable; reopen it through the session controller', 503);
      const imported = files.map(row => ({path: row.kind === 'draft' ? path.join(prepared.rootPath, row.relativePath) :
        prepared.grants.find(grant => grant.source.endsWith(path.sep + row.relativePath.split('/').join(path.sep)))?.path,
        kind: row.kind}));
      const text = '工作区迁移记录：' + JSON.stringify({source_session_id: sessionId,
        source_revision: current.source_revision, files: imported,
        history_preserved: true, production_authority_transferred: false}) +
        '。仅草稿可编辑；证据是只读快照，旧验收和任务控制权未转移。继续生产时依据当前正式接口重新建立任务。';
      // Native inbox insertion is logged without starting model work or rewriting old events.
      agent.inject(createMessage({source: {kind: 'benchforge/workspace-migration', form: 'notice', summary: '工作区迁移来源'},
        content: [{type: 'text', text}]}));
      return {session_id: created.sessionId, workspace: prepared.rootPath, migration_from: prepared.migrationFrom,
        files: imported, history_preserved: true, production_authority_transferred: false};
    };
    const agent = agents.get(sessionId);
    return agent ? agent.runMaintenance(operation) : operation();
  }
  return {preview, migrate};
}
