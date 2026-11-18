import fs from 'node:fs/promises';
import path from 'node:path';
import {isDeepStrictEqual} from 'node:util';
import {z} from 'zod';
import {prepareConversation, relocate} from './archive.mjs';

const relativePath = z.string().min(1).refine(value => !value.includes('\\') &&
  !path.posix.isAbsolute(value) && !value.split('/').some(part => part === '..' || part === '.') && !value.includes(':'));
const Manifest = z.object({schema: z.literal('benchforge.example-bootstrap/v1'), id: z.literal('spatialforge-v1'),
  sessionId: z.string().regex(/^session-[a-zA-Z0-9-]+$/), title: z.string().min(1),
  conversation: relativePath, pathRoots: z.array(z.string().min(1)),
  artifacts: z.array(z.object({path: relativePath, title: z.string().min(1)})).min(1)});
const exists = async file => {try {await fs.access(file); return true;} catch (error) {if (error.code === 'ENOENT') return false; throw error;}};
const inside = (root, file) => {const relative = path.relative(root, file); return relative !== '..' && !relative.startsWith('..' + path.sep) && !path.isAbsolute(relative);};

async function verifyFile(root, relative) {
  // Compare canonical paths on both sides (Windows temp paths may use aliases).
  const canonicalRoot = await fs.realpath(root);
  const file = await fs.realpath(path.join(canonicalRoot, relative));
  if (!inside(canonicalRoot, file) || !(await fs.stat(file)).isFile()) throw new Error('Invalid example file: ' + relative);
  return file;
}

async function relocateAssets(directory, roots, destination) {
  for (const entry of await fs.readdir(directory, {withFileTypes: true})) {
    const file = path.join(directory, entry.name);
    if (entry.isSymbolicLink()) throw new Error('Example assets must not contain symlinks: ' + file);
    if (entry.isDirectory()) await relocateAssets(file, roots, destination);
    else if (/\.(json|jsonl|md|txt|usda)$/.test(entry.name)) {
      const text = await fs.readFile(file, 'utf8'), updated = relocate(text, roots, destination.replaceAll('\\', '/'));
      if (updated !== text) await fs.writeFile(file, updated);
    }
  }
}

async function installAssets(source, workspace, roots, id) {
  const receipt = path.join(workspace, '.benchforge-example.json');
  if (await exists(workspace)) {
    if (!await exists(receipt) || JSON.parse(await fs.readFile(receipt, 'utf8')).id !== id)
      throw new Error('Refusing to overwrite the existing example workspace: ' + workspace);
    return;
  }
  const parent = path.dirname(workspace);
  await fs.mkdir(parent, {recursive: true});
  const stage = await fs.mkdtemp(path.join(parent, '.example-import-'));
  try {
    const target = path.join(stage, 'example', 'SpatialForge');
    await fs.cp(source, target, {recursive: true,
      filter: async file => {if ((await fs.lstat(file)).isSymbolicLink()) throw new Error('Example assets must not contain symlinks'); return true;}});
    await relocateAssets(target, roots, path.join(workspace, 'example', 'SpatialForge'));
    await fs.writeFile(path.join(stage, '.benchforge-example.json'), JSON.stringify({id}));
    await fs.rename(stage, workspace);
  } finally {
    // stage was allocated above directly inside this instance's example directory.
    await fs.rm(stage, {recursive: true, force: true});
  }
}

async function publishWorkbench(source, workspace, sessionId, roots) {
  const asset = path.join(workspace, 'example', 'SpatialForge');
  const manifest = JSON.parse(await fs.readFile(path.join(source, 'bootstrap.json'), 'utf8'));
  if (!manifest.workbench) return;
  if (manifest.sessionId !== sessionId) throw new Error('Example workbench identity differs from the imported session');
  await fs.writeFile(path.join(asset, 'bootstrap.json'), JSON.stringify(manifest, null, 2));
  const binding = path.join(asset, 'request.json');
  if (await exists(binding) && JSON.parse(await fs.readFile(binding, 'utf8')).schema !== 'benchforge.example-import/v1')
    throw new Error('Refusing to replace an unrecognized example workspace binding');
  await fs.writeFile(binding, JSON.stringify({
    schema:'benchforge.example-import/v1', context:{session_id:sessionId}, source:'bundled-example',
  }));
  await relocateAssets(asset, roots, asset.replaceAll('\\', '/'));
}

/** One-time import through DSH services; a durable receipt also preserves user archival/deletion. */
export async function bootstrapExample({sourceDirectory, home, persistence, registry, table, publishSummary, now = Date.now}) {
  const key = 'spatialforge-v1';
  let record = table.get(key);
  if (record?.state === 'complete') {
    if (!record.workbenchVersion && await persistence.stat(record.sessionId)) {
      const source = await fs.realpath(sourceDirectory);
      const manifest = Manifest.parse(JSON.parse(await fs.readFile(path.join(source, 'bootstrap.json'), 'utf8')));
      await publishWorkbench(source, record.workspace, record.sessionId, manifest.pathRoots);
      record = {...record, workbenchVersion:1};
      await table.put(key, record);
    }
    // Upgrade the derived sidebar cache once without restoring deleted examples or changing history.
    if (!record.summaryVersion && await persistence.stat(record.sessionId)) {
      const reader = await persistence.open(record.sessionId, 'read');
      try {await publishSummary(reader.header, (await reader.read()).events);}
      finally {await reader.close();}
      await table.put(key, {...record, summaryVersion: 1});
    }
    return {status: 'existing', sessionId: record.sessionId};
  }
  const source = await fs.realpath(sourceDirectory);
  const manifest = Manifest.parse(JSON.parse(await fs.readFile(path.join(source, 'bootstrap.json'), 'utf8')));
  const prior = await persistence.stat(manifest.sessionId);
  if (prior && !record) return {status: 'existing', sessionId: manifest.sessionId};
  const workspace = record?.workspace || path.resolve(home, 'examples', 'SpatialForge');
  const archive = await verifyFile(source, manifest.conversation);
  for (const file of manifest.artifacts) await verifyFile(source, file.path);
  const time = record?.importedAt || now();
  const assetRoot = path.join(workspace, 'example', 'SpatialForge');
  const {header, events, roots} = await prepareConversation(archive, manifest, workspace, assetRoot, time);
  await installAssets(source, workspace, roots, key);
  await publishWorkbench(source, workspace, header.id, roots);
  for (const file of manifest.artifacts) await verifyFile(assetRoot, file.path);
  record = {state: 'preparing', sessionId: header.id, workspace, importedAt: time};
  await table.put(key, record);
  const handle = prior ? await persistence.open(header.id, 'write') : await persistence.create(header);
  try {
    if (!isDeepStrictEqual(handle.header, header)) throw new Error('Example import metadata changed; refusing to overwrite history');
    const stored = (await handle.read()).events;
    if (stored.length > events.length || stored.some((event, i) => !isDeepStrictEqual(event, events[i])))
      throw new Error('Example import prefix changed; refusing to overwrite history');
    await handle.append(events.slice(stored.length));
    await handle.flush();
  } finally {await handle.close();}
  await publishSummary(header, events);
  const registered = await registry.create(workspace, 'BenchForge 示例');
  await registered.attachSession(header.id);
  await registry.pinSession(header.id);
  await table.put(key, {...record, state: 'complete', summaryVersion: 1, workbenchVersion:1});
  return {status: 'imported', sessionId: header.id, events: events.length, workspace};
}
