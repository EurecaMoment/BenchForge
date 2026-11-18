import fs from 'node:fs/promises';
import path from 'node:path';
import {randomUUID} from 'node:crypto';
import {createWriteStream} from 'node:fs';
import {pipeline} from 'node:stream/promises';

export class AccessError extends Error {
  constructor(message, code = 'BENCHFORGE_ACCESS_DENIED') { super(message); this.code = code; }
}
export const within = (root, value) => {
  const relative = path.relative(root, value);
  return relative === '' || relative !== '..' && !relative.startsWith('..' + path.sep) && !path.isAbsolute(relative);
};
const identity = value => {
  if (typeof value !== 'string' || !value || value.length > 256 || /[\0\r\n]/.test(value))
    throw new AccessError('Invalid session identity');
  return value;
};
const copy = value => structuredClone(value);

async function readOwnedFile(root, source, limit = 16 * 1024 * 1024) {
  const actual = await fs.realpath(source);
  if (!within(root, actual)) throw new AccessError('Input leaves its owning workspace');
  const handle = await fs.open(actual, fs.constants.O_RDONLY | (fs.constants.O_NOFOLLOW || 0));
  try {
    const stat = await handle.stat();
    if (!stat.isFile() || stat.size > limit) throw new AccessError('Input must be a bounded regular file');
    if (process.platform === 'linux' && !within(root, await fs.realpath('/proc/self/fd/' + handle.fd)))
      throw new AccessError('Input moved outside its owning workspace');
    const data = Buffer.alloc(stat.size + 1);
    let offset = 0;
    while (offset < data.length) {
      const {bytesRead} = await handle.read(data, offset, data.length - offset, offset);
      if (!bytesRead) break;
      offset += bytesRead;
    }
    const after = await handle.stat();
    if (offset !== stat.size || after.size !== stat.size || after.mtimeMs !== stat.mtimeMs)
      throw new AccessError('Input changed during handoff; retry after its writer completes');
    return {actual, data: data.subarray(0, offset)};
  } finally { await handle.close(); }
}

async function copyOwnedFile(root, source, destination, signal, version) {
  const actual = await fs.realpath(source);
  if (!within(root, actual)) throw new AccessError('Migration file leaves its approved source workspace');
  const handle = await fs.open(actual, fs.constants.O_RDONLY | (fs.constants.O_NOFOLLOW || 0));
  try {
    const before = await handle.stat();
    if (!before.isFile()) throw new AccessError('Migration entries must be regular files');
    if (version && (before.size !== version.size || before.mtimeMs !== version.mtimeMs))
      throw new AccessError('Migration input changed after owner review; refresh its preview');
    if (process.platform === 'linux' && !within(root, await fs.realpath('/proc/self/fd/' + handle.fd)))
      throw new AccessError('Migration input changed its owning directory');
    await fs.mkdir(path.dirname(destination), {recursive: true, mode: 0o700});
    await pipeline(handle.createReadStream({autoClose: false}), createWriteStream(destination, {flags: 'wx', mode: 0o600}), {signal});
    const after = await handle.stat();
    if (before.size !== after.size || before.mtimeMs !== after.mtimeMs)
      throw new AccessError('Migration input changed; freeze its writer and retry');
  } finally { await handle.close(); }
}

/** Durable Host-owned bindings; no model file or path is an ownership record. */
export class WorkspaceBindings {
  constructor({table, hostId, ownerId, workspaceRoot, resourceRoot}) {
    Object.assign(this, {table, hostId, ownerId, workspaceRoot, resourceRoot});
    this.pending = new Map();
  }
  async initialize() {
    for (const key of ['workspaceRoot', 'resourceRoot']) {
      if (!path.isAbsolute(this[key])) throw new AccessError('Managed roots must be absolute');
      await fs.mkdir(this[key], {recursive: true, mode: 0o700});
      this[key] = await fs.realpath(this[key]);
    }
    if (within(this.workspaceRoot, this.resourceRoot) || within(this.resourceRoot, this.workspaceRoot))
      throw new AccessError('Workspace and read-only resource roots must be disjoint');
  }
  async serial(sessionId, operation) {
    identity(sessionId);
    const task = (this.pending.get(sessionId) || Promise.resolve()).catch(() => {}).then(operation);
    this.pending.set(sessionId, task);
    try { return await task; } finally { if (this.pending.get(sessionId) === task) this.pending.delete(sessionId); }
  }
  get(sessionId) {
    const row = this.table.get(identity(sessionId));
    if (!row) throw new AccessError('This conversation has no managed workspace; explicit migration is required.', 'BENCHFORGE_WORKSPACE_MIGRATION_REQUIRED');
    return copy(row);
  }
  async reserve(sessionId, parentSessionId) {
    return this.serial(sessionId, async () => {
      const prior = this.table.get(sessionId);
      if (prior) {
        if (prior.parentSessionId !== parentSessionId) throw new AccessError('Session parent binding changed');
        await this.verifyRoot(prior);
        return copy(prior);
      }
      if (parentSessionId) {
        const parent = this.get(parentSessionId);
        if (parent.state !== 'active') throw new AccessError('Parent workspace is not active');
      }
      const workspaceId = randomUUID(), rootPath = path.join(this.workspaceRoot, workspaceId);
      await fs.mkdir(rootPath, {mode: 0o700});
      await Promise.all(['tasks', 'inputs', 'outputs'].map(name => fs.mkdir(path.join(rootPath, name))));
      const row = {hostId: this.hostId, ownerId: this.ownerId, sessionId, workspaceId, rootPath,
        state: 'reserved', policyEpoch: 1, rootGeneration: 1, createdAt: Date.now(), grants: [],
        ...(parentSessionId ? {parentSessionId} : {})};
      await this.table.put(sessionId, row);
      return copy(row);
    });
  }
  async verifyRoot(row) {
    let actual;
    try { actual = await fs.realpath(row.rootPath); }
    catch { throw new AccessError('Managed workspace is missing; restore it explicitly.', 'BENCHFORGE_WORKSPACE_UNAVAILABLE'); }
    if (actual !== row.rootPath || !within(this.workspaceRoot, actual) ||
        actual === this.workspaceRoot || !(await fs.stat(actual)).isDirectory())
      throw new AccessError('Managed workspace identity changed');
    return actual;
  }
  async activate(header) {
    return this.serial(header.id, async () => {
      const row = this.get(header.id);
      await this.verifyRoot(row);
      if (!header.cwd || await fs.realpath(header.cwd) !== row.rootPath || header.parentSession !== row.parentSessionId)
        throw new AccessError('Stored conversation directory differs from its managed binding; explicit migration is required.', 'BENCHFORGE_WORKSPACE_MIGRATION_REQUIRED');
      if (row.state !== 'active') { row.state = 'active'; await this.table.put(row.sessionId, row); }
      return copy(row);
    });
  }
  /** Freeze one parent-owned input before a child starts; never grant the parent root. */
  async snapshotInput(childId, parentId, source) {
    return this.serial(childId, async () => {
      const child = this.get(childId), parent = this.get(parentId);
      const {actual, data} = await readOwnedFile(parent.rootPath, source);
      if (child.parentSessionId !== parentId) throw new AccessError('Input parent identity differs');
      const prior = child.grants.find(grant => grant.source === actual);
      if (prior) return prior.path;
      if (child.state !== 'reserved') throw new AccessError('New inputs require an inactive child preparation');
      const destination = path.join(this.resourceRoot, child.workspaceId, randomUUID(), path.basename(actual));
      await fs.mkdir(path.dirname(destination), {recursive: true, mode: 0o700});
      await fs.writeFile(destination, data, {flag: 'wx', mode: 0o400});
      await fs.chmod(destination, 0o444);
      child.grants.push({path: destination, source: actual, kind: 'input', parentSessionId: parentId});
      await this.table.put(childId, child);
      return destination;
    });
  }
  /** Publish a bounded text-only preparation handoff after the Host observes child idle. */
  async exportPreparation(childId, parentId, destination) {
    const child = this.get(childId), parent = this.get(parentId);
    if (child.parentSessionId !== parentId || child.state !== 'active') throw new AccessError('Unknown preparation child');
    const target = await fs.realpath(destination), source = path.join(child.rootPath, 'tasks', 'preparation');
    if (!/^(tasks|synthesis)[\\/]/.test(path.relative(parent.rootPath, target)))
      throw new AccessError('Handoff destination must be a parent draft directory');
    const files = [];
    let total = 0;
    for (const entry of await fs.readdir(source, {withFileTypes: true})) {
      if (!entry.isFile() || !/\.(?:py|js|mjs|ts|json|jsonl|md|ya?ml|txt)$/i.test(entry.name)) continue;
      const {data} = await readOwnedFile(source, path.join(source, entry.name), 2 * 1024 * 1024);
      total += data.length;
      if (files.length >= 64 || total > 16 * 1024 * 1024) throw new AccessError('Preparation handoff exceeds its text-file budget');
      files.push({name: entry.name, data});
    }
    // Never replace a parent edit or a previously delivered revision.
    for (const file of files) {
      const text = file.data.toString('utf8').replaceAll(source, target);
      const destinationFile = path.join(target, file.name);
      try { await fs.writeFile(destinationFile, text, {flag: 'wx', mode: 0o600}); }
      catch (error) {
        if (error.code !== 'EEXIST') throw error;
        const existing = await readOwnedFile(target, destinationFile, 2 * 1024 * 1024);
        if (existing.data.toString('utf8') !== text) throw new AccessError('Parent draft already differs; choose a new handoff directory');
      }
    }
    return {files: files.map(file => path.join(target, file.name)), accepted: false};
  }
  /** Host-derived manifest, after the owner selected native/PG-proven candidates. */
  async migrateFiles(newSessionId, sourceHeader, sourceRevision, files, signal, verifySource = async () => {}) {
    return this.serial(newSessionId, async () => {
      const row = this.get(newSessionId);
      if (row.state !== 'reserved' || row.parentSessionId || row.migrationFrom)
        throw new AccessError('Migration requires a fresh reserved destination conversation');
      const sourceRoot = await fs.realpath(sourceHeader.cwd);
      if (!Array.isArray(files) || files.length > 902) throw new AccessError('An explicitly approved migration file manifest is required');
      const seen = new Set(), entries = files.map(file => {
        const relative = file.relativePath;
        if (typeof relative !== 'string' || path.isAbsolute(relative) || relative.includes('\\') ||
            relative.split('/').some(part => ['', '.', '..'].includes(part)) || !/^(tasks|operations|artifacts)\//.test(relative) ||
            !['draft', 'artifact'].includes(file.kind) || seen.has(relative)) throw new AccessError('Invalid migration manifest entry');
        if (file.kind === 'draft' && !relative.startsWith('tasks/')) throw new AccessError('Only task drafts are writable migration inputs');
        seen.add(relative);
        return file;
      });
      const stage = await fs.mkdtemp(path.join(this.resourceRoot, 'migration-'));
      const drafts = [];
      try {
        for (const file of entries) {
          signal?.throwIfAborted();
          await copyOwnedFile(sourceRoot, path.join(sourceRoot, file.relativePath), path.join(stage, file.relativePath), signal, file.version);
        }
        await verifySource();
        for (const file of entries) {
          const snapshot = path.join(stage, file.relativePath);
          await fs.chmod(snapshot, 0o444);
          if (file.kind === 'draft') {
            const target = path.join(row.rootPath, file.relativePath);
            await fs.mkdir(path.dirname(target), {recursive: true});
            await fs.copyFile(snapshot, target, fs.constants.COPYFILE_EXCL);
            drafts.push(target);
            await fs.chmod(target, 0o600);
          } else {
            row.grants.push({path: snapshot, source: path.join(sourceRoot, file.relativePath),
              kind: 'input', parentSessionId: sourceHeader.id});
          }
        }
        signal?.throwIfAborted();
        await verifySource();
        row.migrationFrom = {sessionId: sourceHeader.id, revision: JSON.stringify(sourceRevision), copiedAt: Date.now()};
        await this.table.put(newSessionId, row);
      } catch (error) {
        await Promise.all(drafts.map(file => fs.rm(file, {force: true})));
        await fs.rm(stage, {recursive: true, force: true});
        throw error;
      }
      if (!row.grants.length) await fs.rm(stage, {recursive: true, force: true});
      return {...copy(row), importedFiles: entries.length, productionAuthorityTransferred: false};
    });
  }
  scope(session) {
    const row = this.get(session.header.id);
    if (row.state !== 'active' || session.header.cwd !== row.rootPath)
      throw new AccessError('Conversation workspace is not active');
    return Object.freeze({host_id: row.hostId, owner_id: row.ownerId, session_id: row.sessionId,
      workspace_id: row.workspaceId, root_path: row.rootPath, root_generation: row.rootGeneration,
      policy_epoch: row.policyEpoch, ...(row.parentSessionId ? {parent_session_id: row.parentSessionId} : {})});
  }
}
