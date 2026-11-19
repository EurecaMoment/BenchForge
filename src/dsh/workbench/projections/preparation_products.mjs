import fs from 'node:fs/promises';
import path from 'node:path';

const SCHEMA = 'benchforge.synthesis-sessions/v1';
const MAX_RECORDS = 8, MAX_METADATA = 64, MAX_FILES = 3, MAX_BYTES = 2 * 1024 * 1024;
const TEXT_FILES = /\.(?:py|js|mjs|ts|md|json|ya?ml)$/i;
const SECRET = /^(?:answers?|answer_?key|correct_?answer|reference_?answer|ground_?truth|gt|authority|gold|oracle|token|password|api_?key|solution)(?:_|$)/i;
/** 判断准备产物路径是否位于已接纳的工作区内。 */
const inside = (root, value) => {
  const relative = path.relative(root, value);
  return relative === '' || relative !== '..' && !relative.startsWith('..' + path.sep) && !path.isAbsolute(relative);
};
/** 构造带 HTTP 状态的准备产物读取错误。 */
function failure(message, status = 403) { return Object.assign(new Error(message), {status}); }
/** 转义文本中的 HTML 特殊字符，供有界预览安全展示。 */
const escape = value => String(value).replace(/[&<>"']/g, character => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[character]));
/** 递归移除敏感字段，同时保留公开的权威来源说明。 */
const clean = value => Array.isArray(value) ? value.map(clean) : value && typeof value === 'object'
  ? Object.fromEntries(Object.entries(value).filter(([key]) => key === 'authority_source' || !SECRET.test(key)).map(([key, child]) => [key, clean(child)])) : value;

/** 通过文件句柄读取大小受限的普通文本文件，并持续检查取消状态。 */
async function text(file, signal) {
  signal?.throwIfAborted();
  const handle = await fs.open(file, 'r');
  try {
    const stat = await handle.stat();
    if (!stat.isFile() || stat.size > MAX_BYTES) throw failure('Preparation file is outside the bounded text preview', 413);
    const buffer = Buffer.alloc(Math.min(MAX_BYTES + 1, stat.size + 1));
    let offset = 0;
    while (offset < buffer.length) {
      signal?.throwIfAborted();
      const {bytesRead} = await handle.read(buffer, offset, buffer.length - offset, offset);
      if (!bytesRead) break;
      offset += bytesRead;
    }
    if (offset > MAX_BYTES) throw failure('Preparation file is too large', 413);
    if (offset > stat.size) throw failure('Preparation file changed during the preview; refresh it', 409);
    return buffer.subarray(0, offset).toString('utf8');
  } finally { await handle.close(); }
}

/** 解析并校验真实目录，拒绝离开已接纳工作区的准备路径。 */
async function directory(root, value) {
  const resolved = await fs.realpath(value);
  if (!inside(root, resolved) || !(await fs.stat(resolved)).isDirectory()) throw failure('Preparation directory leaves its admitted workspace');
  return resolved;
}
/** 把批次键编码为安全文件名，定位准备任务持久化记录。 */
const recordFile = (dir, key) => path.join(dir, 'batch-' + Buffer.from(key, 'utf8').toString('base64url') + '.json');

/** Read registered preparation outputs. Nothing here starts work or accepts a stage. */
/** 创建准备子任务产物读取与预览服务，缓存已核验的回执及发现结果。 */
export function createPreparationProducts({validatePlan}) {
  const receipts = new Map(), discovery = new Map();
  /** 根据会话范围生成准备产物缓存键。 */
  const scopeKey = (root, parentId) => root.path + '\0' + parentId;
  /** 保存已验证的准备回执，供后续预览查找同一批次。 */
  function remember(root, parentId, receipt) {
    if (receipt?.parentId === parentId && typeof receipt.batchKey === 'string' && receipt.recordPath) {
      receipts.set(scopeKey(root, parentId), receipt);
      if (receipts.size > 32) receipts.delete(receipts.keys().next().value);
    }
  }
  /** 从准备记录确认父会话与批次归属，形成允许读取的范围。 */
  async function recordScope(root, parentId) {
    const canonical = await fs.realpath(root.path);
    const dir = await directory(canonical, path.join(canonical, '.benchforge', 'synthesis-sessions'));
    return {canonical, dir, key: scopeKey(root, parentId)};
  }
  /** 从准备批次与子工作区发现可展示的候选文件。 */
  async function candidates(root, parentId, signal) {
    const {canonical, dir, key} = await recordScope(root, parentId);
    const hint = receipts.get(key);
    const known = hint ? recordFile(dir, hint.batchKey) : null;
    if (known) return {canonical, dir, files: [known]};
    let cached = discovery.get(key);
    if (!cached || cached.expires < Date.now()) {
      // Only this small registration directory is listed. Worker datasets and
      // native session storage are never enumerated for artifact discovery.
      const names = (await fs.readdir(dir, {withFileTypes: true})).filter(entry =>
        (entry.isFile() || entry.isSymbolicLink()) && /^batch-[A-Za-z0-9_-]+\.json$/.test(entry.name)).slice(-MAX_METADATA);
      const metadata = await Promise.all(names.map(async entry => {
        signal?.throwIfAborted();
        const file = path.join(dir, entry.name);
        try { return {file, time: (await fs.stat(file)).mtimeMs}; } catch { return null; }
      }));
      cached = {expires: Date.now() + 15000, files: metadata.filter(Boolean).sort((a, b) => b.time - a.time)
        .slice(0, MAX_RECORDS).map(row => row.file)};
      discovery.set(key, cached);
      if (discovery.size > 32) discovery.delete(discovery.keys().next().value);
    }
    return {canonical, dir, files: [...new Set([known, ...cached.files].filter(Boolean))].slice(0, MAX_RECORDS)};
  }
  /** 核对候选产物和子会话工作区绑定，建立可读取的真实路径关系。 */
  async function binding(root, parentId, file, signal) {
    const {canonical, dir} = await recordScope(root, parentId);
    const actual = await fs.realpath(file);
    if (!inside(dir, actual)) throw failure('Preparation record leaves its registration directory');
    let record;
    try { record = JSON.parse(await text(actual, signal)); }
    catch (error) {
      if (error.status || signal?.aborted) throw error;
      throw failure('Preparation batch record is incomplete or invalid', 409);
    }
    const identity = record?.identity;
    if (!record || record.schema !== SCHEMA || record.parentId !== parentId || identity?.parentId !== parentId ||
        typeof record.batchKey !== 'string' || identity.batchKey !== record.batchKey ||
        path.resolve(actual) !== recordFile(dir, record.batchKey) ||
        record.workspacePath !== canonical || identity.workspacePath !== canonical ||
        typeof record.planPath !== 'string' || identity.planPath !== record.planPath ||
        !Array.isArray(record.tasks) || !Array.isArray(identity.tasks) ||
        [...record.tasks, ...identity.tasks].some(row => !row || typeof row !== 'object' || Array.isArray(row)))
      throw failure('Preparation batch identity does not match the current parent, workspace and plan');
    const plan = await validatePlan(root, record.planPath, parentId);
    if (plan !== record.planPath) throw failure('The frozen preparation plan changed its real path');
    return {record, canonical};
  }
  /** 读取单个准备任务的产物与原生状态，整理为工作台展示条目。 */
  async function worker(root, parentId, file, childId, signal, bound) {
    const {record, canonical} = bound || await binding(root, parentId, file, signal);
    const task = record.tasks.find(row => row.childId === childId);
    const original = task && record.identity.tasks.find(row => row.key === task.key);
    if (!task || !original || task.outputDirectory !== original.outputDirectory || task.label !== original.label)
      throw failure('Preparation worker does not match its admitted output directory');
    const output = await directory(canonical, task.outputDirectory);
    if (output === canonical || output !== path.resolve(original.outputDirectory)) throw failure('The worker output changed its admitted real path');
    const entries = (await fs.readdir(output, {withFileTypes: true})).filter(entry =>
      (entry.isFile() || entry.isSymbolicLink()) && TEXT_FILES.test(entry.name) && !/^(?:answers?|authority|ground_truth|gold|dataset|collection)(?:[._-]|$)/i.test(entry.name));
    /** 为准备产物预览排序，优先展示更适合作为设计和说明入口的文件。 */
    const priority = name => name.toLowerCase() === 'summary.md' ? 0 : name.toLowerCase() === 'manifest.json' ? 1 : 2;
    entries.sort((a, b) => priority(a.name) - priority(b.name) || a.name.localeCompare(b.name));
    const files = [];
    for (const entry of entries) {
      if (files.length >= MAX_FILES) break;
      signal?.throwIfAborted();
      try {
        const source = await fs.realpath(path.join(output, entry.name));
        const stat = await fs.stat(source);
        if (!inside(output, source) || !stat.isFile() || stat.size > MAX_BYTES) continue;
        files.push({file: entry.name, source, bytes: stat.size, revision: stat.mtimeMs + ':' + stat.size,
          title: entry.name.toLowerCase() === 'summary.md' ? '准备摘要' : entry.name.toLowerCase() === 'manifest.json' ? '准备清单' : '脚本准备稿 · ' + entry.name,
          kind: 'preparation', accepted: false});
      } catch (error) { if (signal?.aborted) throw error; }
    }
    return {childId, batchKey: record.batchKey, label: task.label, planPath: record.planPath,
      reasoningSelection: task.reasoningSelection || record.reasoningSelection,
      status: 'preparing', accepted: false, files};
  }
  /** 读取当前会话拥有的准备批次及产物目录。 */
  async function read(root, parentId, children, signal) {
    if (!children.length) return new Map();
    let selected;
    try { selected = await candidates(root, parentId, signal); }
    catch (error) { if (signal?.aborted) throw error; return new Map(); }
    const products = new Map();
    for (const file of selected.files) {
      signal?.throwIfAborted();
      let bound;
      try { bound = await binding(root, parentId, file, signal); }
      catch (error) { if (signal?.aborted) throw error; continue; }
      for (const child of children) {
        if (products.has(child.id) || !bound.record.tasks.some(task => task.childId === child.id)) continue;
        try { products.set(child.id, await worker(root, parentId, file, child.id, signal, bound)); }
        catch (error) { if (signal?.aborted) throw error; }
      }
    }
    return products;
  }
  /** 按已验证的批次及文件标识打开有界预览，拒绝越界与未经登记的文件。 */
  async function open(root, parentId, children, {batchKey, childId, file}, signal) {
    if (!children.some(child => child.id === childId)) throw failure('Preparation child is not in the verified parent family');
    if (typeof batchKey !== 'string' || !batchKey || typeof file !== 'string' || path.basename(file) !== file || file.includes('\\'))
      throw failure('Preparation preview requires an admitted file');
    const {dir} = await recordScope(root, parentId);
    const product = await worker(root, parentId, recordFile(dir, batchKey), childId, signal);
    const artifact = product.files.find(row => row.file === file);
    if (!artifact) throw failure('Preparation file is not an admitted text artifact', 404);
    let content = await text(artifact.source, signal);
    if (/\.json$/i.test(file)) {
      let document;
      try { document = JSON.parse(content); }
      catch { throw failure('Preparation JSON is still being written or needs correction', 409); }
      const publicDocument = file.toLowerCase() === 'manifest.json' ? Object.fromEntries(
        ['status', 'summary', 'checks', 'missing_inputs', 'unresolved', 'notes']
          .filter(key => document && typeof document === 'object' && key in document).map(key => [key, document[key]])) : document;
      content = JSON.stringify(clean(publicDocument), null, 2);
    }
    const title = product.label + ' · ' + artifact.title;
    const html = '<!doctype html><html lang="zh"><meta charset="utf-8"><title>' + escape(title) + '</title>' +
      '<style>:root{color-scheme:light dark}body{max-width:70rem;margin:2rem auto;padding:0 1.2rem;font:16px/1.7 system-ui,sans-serif}h1{font-size:1.35rem}p{opacity:.75}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:14px/1.65 ui-monospace,monospace}</style>' +
      '<h1>' + escape(title) + '</h1><p>这是供主流程合并使用的准备产物，尚未完成场景采集、标注或题目验收。</p><pre>' +
      escape(content) + '</pre></html>';
    return new Response(html, {headers: {'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store',
      'x-content-type-options': 'nosniff', 'content-security-policy': "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'self'"}});
  }
  return {remember, read, open};
}
