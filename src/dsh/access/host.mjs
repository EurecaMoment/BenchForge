import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {randomUUID} from 'node:crypto';
import Schema from '@deepseek-ai/schemastery';
import {defineDomain, domainTable} from '@deepseek-ai/dsh-storage-domain';
import {z} from 'zod';
import {AccessError, WorkspaceBindings, within} from './workspaces.mjs';
import {runWithAccessScope} from './context.mjs';
import {systemReadRoots} from './runtime.mjs';
import {publishResources} from './resources.mjs';
import {lookupWorkspaceRoots, retireSessionRegistrations} from './workspace_registry.mjs';

export const name = 'benchforge-access';
export const inject = ['storageDomain', 'workspaceRegistry', 'sandboxPolicy'];
export const Config = Schema.object({
  workspaceRoot: Schema.string().required(), resourceRoot: Schema.string().required(),
  ownerId: Schema.string().default('host-owner'), readOnlyRoots: Schema.array(Schema.string()).default([]),
  skillsSource: Schema.string(), supportSource: Schema.string(),
});
const bindings = defineDomain({name: 'benchforge_access', version: 1, tables: {
  identity: domainTable(z.object({hostId: z.string(), sessionRegistrationsRetired: z.boolean().optional()})),
  workspaces: domainTable(z.object({hostId: z.string(), ownerId: z.string(), sessionId: z.string(),
    workspaceId: z.string(), rootPath: z.string(), registeredProject: z.literal(true).optional(),
    parentSessionId: z.string().optional(), artifactRootId: z.string().optional(),
    state: z.enum(['reserved', 'active']), policyEpoch: z.number().int().positive(),
    rootGeneration: z.number().int().positive(), createdAt: z.number(),
    migrationFrom: z.object({sessionId: z.string(), revision: z.string(), copiedAt: z.number()}).optional(),
    grants: z.array(z.object({path: z.string(), source: z.string(), kind: z.literal('input'), parentSessionId: z.string()})),
  })),
}});

/** A Host service, deliberately not a Remote or a model tool. */
/** 装配受管工作区、只读资源和会话授权服务，并把访问策略的注册与释放绑定到宿主及会话生命周期。 */
export async function apply(ctx, config) {
  if (typeof ctx.sandboxPolicy.registerReadAccess !== 'function')
    throw new AccessError('BenchForge requires the native read-access policy extension', 'BENCHFORGE_ACCESS_UNAVAILABLE');
  const domain = await ctx.storageDomain.open(bindings);
  ctx.effect(() => () => domain.close(), name);
  const table = domain.table('identity');
  let host = table.get('host');
  if (!host) { host = {hostId: randomUUID()}; await table.put('host', host); }
  const store = new WorkspaceBindings({table: domain.table('workspaces'), ...host,
    ownerId: config.ownerId, workspaceRoot: config.workspaceRoot, resourceRoot: config.resourceRoot});
  await store.initialize();
  if (!host.sessionRegistrationsRetired) {
    await retireSessionRegistrations(store, ctx.workspaceRegistry);
    await table.put('host', {...host, sessionRegistrationsRetired: true});
  }
  const packageDirectory = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
  const skillsRoot = await publishResources(config.skillsSource || path.join(packageDirectory, 'skills'),
    store.resourceRoot, 'skills', /\.md$/i);
  const supportSource = config.supportSource || path.join(packageDirectory, 'dist', 'task_support');
  const publishedRoots = [skillsRoot];
  try { publishedRoots.push(await publishResources(supportSource, store.resourceRoot, 'task-support', /\.(?:md|py|json)$/i)); }
  catch (error) { if (error.code !== 'ENOENT') throw error; }
  const roots = [...await systemReadRoots(), ...await Promise.all(config.readOnlyRoots.map(/** 解析显式只读根目录，拒绝盘符根目录、受管目录重叠及不符合来源标记的路径。 */ async value => {
    if (!path.isAbsolute(value)) throw new AccessError('Read-only resource roots must be absolute');
    const actual = await fs.realpath(value);
    if (actual === path.parse(actual).root || within(actual, store.workspaceRoot) || within(actual, store.resourceRoot))
      throw new AccessError('Read-only resource grant must not expose other managed workspaces');
    if (/(?:^|[\\/])(?:third_party|\.deployment|\.ssh|\.aws|\.git|config)(?:[\\/]|$)/i.test(actual))
      throw new AccessError('Read-only resources must be published outside source, deployment and credential directories');
    for (const marker of ['.git', 'conda-meta']) {
      if (await fs.access(path.join(actual, marker)).then(() => true, error => {
        if (['ENOENT', 'ENOTDIR'].includes(error.code)) return false;
        throw error;
      })) throw new AccessError('Repository and whole Conda environment grants are not permitted');
    }
    return actual;
  }))];
  const active = new WeakMap();
  /** 根据预设身份或已有绑定判断会话是否受 BenchForge 工作区管理。 */
  const managed = session => session?.header.agentPreset === 'benchforge' ||
    Boolean(session?.header.id && domain.table('workspaces').get(session.header.id));
  /** 同时检查已绑定会话和正在组合的预设，识别需要受管授权的 Agent。 */
  const managedAgent = agent => managed(agent?.session) ||
    agent?.ctx?.get?.('agentPresets')?.composedPreset(agent.ctx) === 'benchforge';
  const service = {
    /** 按可信会话绑定查询工作台与采集可用的文件根目录。 */
    rootsFor: (sessionIds, configuredPaths) => lookupWorkspaceRoots(store, ctx.workspaceRegistry, sessionIds, configuredPaths),
    /** 按会话标识读取受管工作区绑定。 */
    get: id => store.get(id), /** 从已激活的会话工作区构造可信访问作用域。 */ scope: session => store.scope(session),
    /** 为会话预留工作区，并记录可选的父会话归属。 */
    reserve: (id, parentId) => store.reserve(id, parentId),
    /** 把父工作区中的已授权输入制作成供子会话读取的不可变快照。 */
    snapshotInput: (id, parentId, source) => store.snapshotInput(id, parentId, source),
    /** 把子会话准备好的文件导出到父会话指定的草稿目录。 */
    exportPreparation: (id, parentId, destination) => store.exportPreparation(id, parentId, destination), skillsRoot,
    // Host administration only: callers must obtain the file manifest from
    // authorized database records or an explicit owner review, never tool args.
    /** 核验已停止的源会话及其版本，再为显式选择的文件准备新会话迁移。 */
    prepareMigration: async ({sourceSessionId, newSessionId, sourceRevision, files}, signal) => {
      if (ctx.get('agents')?.get(sourceSessionId)?.status === 'running')
        throw new AccessError('Stop the source conversation before migrating its files');
      const query = ctx.get('sessionQuery');
      if (!query) throw new AccessError('Official session query service is unavailable');
      const observed = await query.observeSession(sourceSessionId, {signal, projectionMode: 'none'});
      try {
        const revision = observed.revision ?? {cursor: observed.cursor};
        if (observed.header.id !== sourceSessionId || !observed.header.cwd ||
            sourceRevision === undefined || JSON.stringify(revision) !== JSON.stringify(sourceRevision))
          throw new AccessError('Source conversation identity or revision changed');
        if (sourceSessionId === newSessionId) throw new AccessError('Migration must create a new conversation');
        await store.reserve(newSessionId);
        /** 迁移提交前重新核对源会话的目录、版本和运行状态，拒绝预览后发生的变化。 */
        const verifySource = async () => {
          const current = await query.observeSession(sourceSessionId, {signal, projectionMode: 'none'});
          try {
            if (ctx.get('agents')?.get(sourceSessionId)?.status === 'running' ||
                current.header.cwd !== observed.header.cwd ||
                JSON.stringify(current.revision ?? {cursor: current.cursor}) !== JSON.stringify(sourceRevision))
              throw new AccessError('Source conversation changed during migration; refresh its preview');
          } finally { current[Symbol.dispose](); }
        };
        return await store.migrateFiles(newSessionId, observed.header, sourceRevision, files, signal, verifySource);
      } finally { observed[Symbol.dispose](); }
    },
  };
  ctx.provide('benchforgeAccess', service);
  ctx.on('agent/create', /** 创建受管会话前预留独立工作区，并核对父会话与传入目录的绑定。 */ async (request, next) => {
    const options = request.options;
    const parentId = options.meta?.parentSession;
    const parentBound = parentId && domain.table('workspaces').get(parentId);
    if (options.meta?.agentPreset !== 'benchforge' && !parentBound) return next();
    // 原生控制器已把选中的工作区解析为真实目录；只复用注册表内匹配的顶层项目。
    const project = !parentId && ctx.workspaceRegistry.list().find(entry => entry.path === options.meta?.cwd);
    const row = await store.reserve(options.sessionId, parentId, project || undefined);
    request.options = {...options, meta: {...options.meta, agentPreset: options.meta?.agentPreset || 'benchforge', cwd: row.rootPath}};
    return next();
  });
  /** 校验会话工作区绑定并安装新一代只读访问策略；随 Agent 释放授权。 */
  async function activate(agent) {
    if (!managedAgent(agent)) return;
    const row = await store.activate(agent.session.header);
    const current = ctx.sandboxPolicy.resolve({session: agent.session}).readAccess;
    const executionGeneration = Math.max(row.policyEpoch, current ? current.epoch + 1 : 1);
    const dispose = ctx.sandboxPolicy.registerReadAccess(agent.session, {
      roots: [row.rootPath, ...roots, ...publishedRoots, ...row.grants.map(grant => grant.path)],
      policyId: row.workspaceId, epoch: executionGeneration,
    });
    active.set(agent.session, row.policyEpoch);
    agent.ctx.effect(() => () => { active.delete(agent.session); dispose(); }, name);
  }
  ctx.on('agent/created', /** Agent 完成创建后激活其工作区及只读访问策略。 */ async ({agent}) => activate(agent));
  for (const agent of ctx.get('agents')?.list() || []) {
    // Existing unbound conversations remain readable in the UI; tools reject
    // their scope until an explicit migration has established a trusted root.
    try { await activate(agent); }
    catch (error) {
      if (error.code !== 'BENCHFORGE_WORKSPACE_MIGRATION_REQUIRED') throw error;
      const previous = ctx.sandboxPolicy.resolve({session: agent.session}).readAccess;
      ctx.sandboxPolicy.registerReadAccess(agent.session, {roots: [],
        policyId: previous?.policyId || 'migration-required:' + agent.id, epoch: (previous?.epoch || 0) + 1});
      ctx.logger(name).warn('Conversation %s requires workspace migration', agent.id);
    }
  }
  ctx.on('tools/execute', /** 工具执行前核对授权代际，并在可信异步作用域内调用官方执行链。 */ async (exec, next) => {
    const session = exec.agent?.session;
    if (managedAgent(exec.agent)) {
      const row = store.get(session.header.id);
      if (active.get(session) !== row.policyEpoch) throw new AccessError('Conversation access policy is not active');
      return runWithAccessScope(store.scope(session), next);
    }
    return next();
  }, {prepend: true});
  ctx.on('working-directory/access', /** 除已获完整访问权限的会话外，限制受管会话只能访问自己的绑定工作目录。 */ async (request, next) => {
    if (!managedAgent(request.agent)) return next();
    const row = store.get(request.agent.id);
    if (ctx.sandboxPolicy.resolve({session: request.agent.session}).mode === 'danger-full-access') return next();
    const current = ctx.get('workingDirectory')?.get(request.agent.session) || row.rootPath;
    const requested = request.path === null ? current : path.resolve(current, request.path);
    if (!within(row.rootPath, await fs.realpath(requested)))
      throw new AccessError('Working directory must remain inside this conversation workspace');
    return next();
  });
}
