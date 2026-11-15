import fs from 'node:fs/promises';
import path from 'node:path';
import {ARTIFACT_PROJECTION} from './projection.mjs';
import {ApiError, workspaceRoots} from '../../host/session_scope.mjs';

const contains = (directory, candidate) => {
  const relative = path.relative(directory, candidate);
  return relative !== '..' && !relative.startsWith('..' + path.sep) && !path.isAbsolute(relative);
};

/** Read the registered projection at DSH's live/prepared cut; never rescan session logs. */
export function createArtifactEvidenceReader(query) {
  return async (sessionId, related, signal) => {
    const ids = [sessionId, ...related.map((row) => row.id)];
    return Promise.all(ids.map(async (id) => {
      const observation = await query.observeSession(id, {signal});
      try {
        const view = observation.projections?.values[ARTIFACT_PROJECTION];
        if (!view) throw new ApiError('Conversation artifact projection is unavailable', 503);
        return {sessionId: id, cwd: observation.header.cwd || '', ...view};
      } finally {observation[Symbol.dispose]();}
    }));
  };
}

/** Combine only proven production workspaces with the requested registered workspace. */
export async function resolveArtifactRoots(config, registered, requestedPath, evidence) {
  const current = await workspaceRoots(config, registered, requestedPath);
  const authorized = [...registered, ...(config.workspacePaths || []).map((directory) => ({
    id: 'path:' + directory, path: directory, title: path.basename(directory),
  }))];
  const allowed = [];
  for (const root of authorized) {
    try {allowed.push({...root, path: await fs.realpath(root.path)});}
    catch (error) {if (error.code !== 'ENOENT') throw error;}
  }
  const proven = new Map();
  for (const owner of evidence) {
    const hints = [owner.primaryWorkspace, ...owner.workspaces, owner.cwd,
      ...(owner.files || []).map((file) => path.dirname(file.path))].filter(Boolean);
    for (const directory of hints) {
    try {
      const canonical = await fs.realpath(directory);
      const root = allowed.filter((row) => row.path === canonical || contains(row.path, canonical))
        .sort((a, b) => b.path.length - a.path.length)[0];
      if (root) proven.set(root.path, root);
    } catch (error) {if (error.code !== 'ENOENT') throw error;}
    }
  }
  const primaryPath = evidence[0]?.primaryWorkspace;
  let primary;
  if (primaryPath) {
    try {
      const canonical = await fs.realpath(primaryPath);
      primary = [...proven.values()].find((root) => root.path === canonical || contains(root.path, canonical));
    }
    catch (error) {if (error.code !== 'ENOENT') throw error;}
  }
  const roots = new Map();
  for (const root of [primary, ...current, ...proven.values()].filter(Boolean)) if (!roots.has(root.path)) roots.set(root.path, root);
  return [...roots.values()];
}
