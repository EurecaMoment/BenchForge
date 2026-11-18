import {readFile} from 'node:fs/promises';
import {zstdDecompressSync} from 'node:zlib';
import {SESSION_FORMAT_VERSION, interruptedTurnClosers} from '@deepseek-ai/dsh-session';
import {assertContiguous, validateStoredEvents} from '@deepseek-ai/dsh-session-persistence';

/** JSONL persistence writes independent Zstandard frames, including a header-only frame. */
export function decodeArchive(bytes) {
  const parts = [];
  let offset = 0, size = 0;
  while (offset < bytes.length) {
    const {buffer, engine} = zstdDecompressSync(bytes.subarray(offset), {info: true});
    if (!engine.bytesWritten) throw new Error('Example archive decoder made no progress');
    offset += engine.bytesWritten;
    size += buffer.length;
    if (size > 256 * 1024 * 1024) throw new Error('Example conversation exceeds 256 MiB');
    parts.push(buffer);
  }
  const [physical, ...events] = Buffer.concat(parts).toString('utf8').trim().split('\n').map(JSON.parse);
  if (physical?.type !== 'session' || physical.version !== SESSION_FORMAT_VERSION || physical.isSeeded)
    throw new Error('Example must contain a complete current-format root conversation');
  const {type, ...header} = physical;
  assertContiguous(header.id, events, 0);
  validateStoredEvents(header, events);
  return {header, events};
}

/** Relocate only declared absolute path roots, including paths inside encoded tool JSON. */
export function relocate(value, roots, destination) {
  if (typeof value === 'string') {
    for (const root of roots) value = value.replaceAll(root, (match, offset, text) => {
      const next = text[offset + match.length];
      return next && /[A-Za-z0-9_.-]/.test(next) ? match : destination;
    });
    return value;
  }
  if (Array.isArray(value)) return value.map(child => relocate(child, roots, destination));
  if (value && typeof value === 'object') return Object.fromEntries(
    Object.entries(value).map(([key, child]) => [key, relocate(child, roots, destination)]));
  return value;
}

/** Import history without starting an Agent or carrying the source machine's permissions. */
export async function prepareConversation(file, manifest, workspace, assetRoot, time) {
  const source = decodeArchive(await readFile(file));
  if (source.header.id !== manifest.sessionId) throw new Error('Example conversation identity differs from its manifest');
  const header = {...source.header, cwd: workspace, agentPreset: 'benchforge'};
  const roots = [...new Set([source.header.cwd, ...manifest.pathRoots].filter(Boolean))].sort((a, b) => b.length - a.length);
  const events = relocate(source.events, roots, assetRoot.replaceAll('\\', '/'));
  events.push(...interruptedTurnClosers(events));
  const append = (type, data) => events.push({type, seq: events.length, time, data});
  append('agent-preset/selected', {agentPreset: 'benchforge'});
  append('working-directory/change', {cwd: workspace});
  append('permission/preset', {preset: 'workspace-write'});
  append('sandbox/mode', {mode: 'workspace-write'});
  append('approval/policy', {policy: 'ask'});
  append('session/title', {title: manifest.title, messageSeqs: [], source: {kind: 'user'}});
  // This declares the files actually installed by the importer, not a new production run.
  append('deliverables/presented', {turn: events.findLast(event => event.type === 'turn/end')?.data.turn || 0,
    callId: 'benchforge-example-import', files: manifest.artifacts.map(file => ({
      path: assetRoot.replaceAll('\\', '/') + '/' + file.path, description: file.title,
    }))});
  assertContiguous(header.id, events, 0);
  validateStoredEvents(header, events);
  return {header, events, roots};
}
