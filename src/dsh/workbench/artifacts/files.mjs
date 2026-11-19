import fs from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {Readable} from 'node:stream';
import path from 'node:path';
import {ApiError, inside, readJson} from '../../host/session_scope.mjs';
import {openOwnedFile} from '../../host/owned_file.mjs';

const FORMATS = {
  '.png': ['image', 'image/png'], '.jpg': ['image', 'image/jpeg'], '.jpeg': ['image', 'image/jpeg'],
  '.webp': ['image', 'image/webp'], '.gif': ['image', 'image/gif'], '.avif': ['image', 'image/avif'],
  '.mp4': ['video', 'video/mp4'], '.webm': ['video', 'video/webm'], '.mov': ['video', 'video/quicktime'],
  '.mp3': ['audio', 'audio/mpeg'], '.wav': ['audio', 'audio/wav'], '.ogg': ['audio', 'audio/ogg'],
  '.json': ['json', 'application/json'], '.jsonl': ['text', 'application/x-ndjson'],
  '.txt': ['text', 'text/plain'], '.md': ['text', 'text/markdown'], '.csv': ['text', 'text/csv'],
};
const STAGES = [
  ['S1', /(?:^|[_.-])(plan|brief|blueprint)(?:[_.-]|$)/i],
  ['S5', /report|quality|score|evaluat|validation/i],
  ['S3', /annotat|depth|normal|semantic|instance|ground.?truth|trajectory|episode|sensor|observation|(?:^|[_.-])collection(?:[_.-]|$)/i],
  ['S4', /question|dataset|benchmark|synthesi/i],
];
/** 对产物拥有者与文件路径计算稳定摘要，作为当前会话中的文件标识。 */
const identity = (owner, file) => createHash('sha256').update(JSON.stringify([owner, file])).digest('base64url');
/** 判断真实文件路径是否位于指定工作区根目录内。 */
const contains = (root, file) => {
  const relative = path.relative(root.path, file);
  return relative !== '..' && !relative.startsWith('..' + path.sep) && !path.isAbsolute(relative);
};

/** 为产物选择最具体的授权根目录，并同时执行路径归属和场景删除策略校验。 */
async function locate(ref, roots) {
  const file = await fs.realpath(ref.path);
  const root = roots.filter((row) => contains(row, file)).sort((a, b) => b.path.length - a.path.length)[0];
  if (!root) throw new ApiError('This artifact is outside the conversation workspaces', 403);
  await inside(root, file);
  // Explicit deliveries obey the same removal policy as indexed pipeline products.
  for (const candidate of roots.filter((row) => contains(row, file))) {
    const policy = await readJson(path.join(candidate.path, 'scene-library', 'policy.json'));
    if (policy?.enabled && (policy.excluded_paths || []).some((folder) => file === folder || file.startsWith(folder + path.sep))) {
      throw new ApiError('此产物使用的场景已删除', 410);
    }
  }
  const stat = await fs.stat(file);
  if (!stat.isFile()) throw new ApiError('The declared artifact is not a file', 404);
  return {root, file, stat};
}

/** 解析所有授权根目录的真实路径，忽略已不存在的目录。 */
async function canonicalRoots(roots) {
  return (await Promise.all(roots.map(async (root) => {
    try { return {...root, path: await fs.realpath(root.path)}; }
    catch (error) { if (error.code === 'ENOENT') return null; throw error; }
  }))).filter(Boolean);
}

/** 合并会话族的显式文件证据；重复路径以父会话事实优先，再按事件序号排序。 */
function references(evidence) {
  const files = new Map();
  for (const owner of evidence) for (const ref of owner.files) {
    const key = ref.path;
    const old = files.get(key);
    // Parent facts win over a child's duplicate. Sequence numbers are session-local.
    if (!old) files.set(key, {...ref, owner: owner.sessionId, id: identity(owner.sessionId, ref.path)});
  }
  return [...files.values()].sort((a, b) => b.seq - a.seq);
}

/** 将原生会话文件证据转换为有界产物目录，附加格式、可读性及文件访问链接。 */
export async function conversationProducts({sessionId, evidence, roots, maxArtifacts = 300, signal}) {
  roots = await canonicalRoots(roots);
  const refs = references(evidence), items = [];
  for (const ref of refs.slice(0, maxArtifacts)) {
    signal?.throwIfAborted();
    const name = path.basename(ref.path), format = path.extname(name).toLowerCase();
    const [kind, contentType] = FORMATS[format] || ['file', 'application/octet-stream'];
    const stage = ref.stage || STAGES.find(([, pattern]) => pattern.test(name))?.[0] ||
      (/(?:^|[\\/])(?:episodes|trajectories|observations|sensors|collection)[\\/]/i.test(ref.path) ? 'S3' : null) ||
      (['image', 'video'].includes(kind) ? 'S2' : null);
    const base = {id: ref.id, name, title: ref.title || name, stage, kind, content_type: contentType,
      source: ref.source, session_id: ref.owner, status: 'available', bytes: null, updated_at: null, url: null};
    try {
      const {root, stat} = await locate(ref, roots);
      const query = new URLSearchParams({view: 'conversation_artifact', artifact: ref.id,
        session_id: sessionId, workspace_path: root.path});
      items.push({...base, bytes: stat.size, updated_at: stat.mtime.toISOString(),
        url: 'api/benchforge/workbench?' + query});
    } catch (error) {
      if (signal?.aborted) throw error;
      if (error.code !== 'ENOENT' && !(error instanceof ApiError)) throw error;
      items.push({...base, status: error.status === 410 ? 'excluded' : error.status === 403 ? 'unavailable' : 'missing'});
    }
  }
  return {session_id: sessionId, workspace_path: roots[0]?.path || null,
    revision: evidence.map((owner) => owner.sessionId + ':' + owner.revision).join(','),
    items, total: refs.length, truncated: refs.length > items.length};
}

/** Serve only a file proven by the current conversation projection; never accept a browser path. */
/** 仅打开当前会话明确产生或交付的文件，校验归属并支持安全的媒体范围读取。 */
export async function openConversationArtifact(id, {evidence, roots, request}) {
  const ref = references(evidence).find((row) => row.id === id);
  if (!ref) throw new ApiError('This file was not produced or delivered in the current conversation', 404);
  roots = await canonicalRoots(roots);
  const {file, root} = await locate(ref, roots);
  const {handle, stat} = await openOwnedFile(root.path, file, request.signal);
  try {
  const [kind, contentType] = FORMATS[path.extname(file).toLowerCase()] || ['file', 'application/octet-stream'];
  const headers = new Headers({
    'content-type': contentType, 'cache-control': 'private, no-cache', 'x-content-type-options': 'nosniff',
    'accept-ranges': 'bytes', 'content-security-policy': "default-src 'none'; sandbox",
    'content-disposition': (kind === 'file' ? 'attachment' : 'inline') + "; filename*=UTF-8''" + encodeURIComponent(path.basename(file)),
  });
  let start = 0, end = stat.size - 1, status = 200;
  const range = request.headers.get('range');
  if (range) {
    const match = /^bytes=(\d*)-(\d*)$/.exec(range);
    if (!match || !match[1] && !match[2]) { await handle.close(); return rangeFailure(headers, stat.size); }
    start = match[1] ? Number(match[1]) : Math.max(0, stat.size - Number(match[2]));
    end = match[1] && match[2] ? Math.min(Number(match[2]), end) : end;
    if (!Number.isSafeInteger(start) || !Number.isSafeInteger(end) || start > end || start >= stat.size || start < 0) {
      await handle.close(); return rangeFailure(headers, stat.size);
    }
    headers.set('content-range', `bytes ${start}-${end}/${stat.size}`); status = 206;
  }
  headers.set('content-length', String(Math.max(0, end - start + 1)));
  const body = stat.size ? Readable.toWeb(handle.createReadStream({start, end, signal: request.signal})) : null;
  if (!stat.size) await handle.close();
  return new Response(body, {status, headers});
  } catch (error) { await handle.close(); throw error; }
}

/** 返回标准的不可满足字节范围响应，并声明文件完整大小。 */
function rangeFailure(headers, size) {
  headers.set('content-range', 'bytes */' + size);
  return new Response(null, {status: 416, headers});
}
