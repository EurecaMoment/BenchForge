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

export const name = 'benchforge-access';
export const inject = ['storageDomain', 'workspaceRegistry', 'sandboxPolicy'];
export const Config = Schema.object({
  workspaceRoot: Schema.string().required(), resourceRoot: Schema.string().required(),
  ownerId: Schema.string().default('host-owner'), readOnlyRoots: Schema.array(Schema.string()).default([]),
  skillsSource: Schema.string(), supportSource: Schema.string(),
});
const bindings = defineDomain({name: 'benchforge_access', version: 1, tables: {
  identity: domainTable(z.object({hostId: z.string()})),
  workspaces: domainTable(z.object({hostId: z.string(), ownerId: z.string(), sessionId: z.string(),
    workspaceId: z.string(), rootPath: z.string(), parentSessionId: z.string().optional(),
    state: z.enum(['reserved', 'active']), policyEpoch: z.number().int().positive(),
    rootGeneration: z.number().int().positive(), createdAt: z.number(),
    migrationFrom: z.object({sessionId: z.string(), revision: z.string(), copiedAt: z.number()}).optional(),
    grants: z.array(z.object({path: z.string(), source: z.string(), kind: z.literal('input'), parentSessionId: z.string()})),
  })),
}});

/** A Host service, deliberately not a Remote or a model tool. */
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
  const packageDirectory = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
  const skillsRoot = await publishResources(config.skillsSource || path.join(packageDirectory, 'skills'),
    store.resourceRoot, 'skills', /\.md$/i);
  const supportSource = config.supportSource || path.join(packageDirectory, 'dist', 'task_support');
  const publishedRoots = [skillsRoot];
  try { publishedRoots.push(await publishResources(supportSource, store.resourceRoot, 'task-support', /\.(?:md|py|json)$/i)); }
  catch (error) { if (error.code !== 'ENOENT') throw error; }
  const roots = [...await systemReadRoots(), ...await Promise.all(config.readOnlyRoots.map(async value => {
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
  const managed = session => session?.header.agentPreset === 'benchforge' ||
    Boolean(session?.header.id && domain.table('workspaces').get(session.header.id));
  const managedAgent = agent => managed(agent?.session) ||
    agent?.ctx?.get?.('agentPresets')?.composedPreset(agent.ctx) === 'benchforge';
  const service = {
    get: id => store.get(id), scope: session => store.scope(session),
    reserve: (id, parentId) => store.reserve(id, parentId),
    snapshotInput: (id, parentId, source) => store.snapshotInput(id, parentId, source),
    exportPreparation: (id, parentId, destination) => store.exportPreparation(id, parentId, destination), skillsRoot,
    // Host administration only: callers must obtain the file manifest from
    // authorized database records or an explicit owner review, never tool args.
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
  ctx.on('agent/create', async (request, next) => {
    const options = request.options;
    const parentId = options.meta?.parentSession;
    const parentBound = parentId && domain.table('workspaces').get(parentId);
    if (options.meta?.agentPreset !== 'benchforge' && !parentBound) return next();
    const row = await store.reserve(options.sessionId, options.meta?.parentSession);
    request.options = {...options, meta: {...options.meta, agentPreset: options.meta?.agentPreset || 'benchforge', cwd: row.rootPath}};
    return next();
  });
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
    const workspace = await ctx.workspaceRegistry.create(row.rootPath, 'BenchForge');
    await workspace.attachSession(agent.id);
  }
  ctx.on('agent/created', async ({agent}) => activate(agent));
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
  ctx.on('tools/execute', async (exec, next) => {
    const session = exec.agent?.session;
    if (managedAgent(exec.agent)) {
      const row = store.get(session.header.id);
      if (active.get(session) !== row.policyEpoch) throw new AccessError('Conversation access policy is not active');
      return runWithAccessScope(store.scope(session), next);
    }
    return next();
  }, {prepend: true});
  ctx.on('working-directory/access', async (request, next) => {
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
