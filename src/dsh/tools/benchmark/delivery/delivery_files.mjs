// Inspect the view metadata in the directory the model actually presented.
import fs from 'node:fs/promises';
import path from 'node:path';

export const fileReviewKind = 'benchforge-delivery-files';
const sceneTools = new Set(['benchforge_capture', 'benchforge_refine', 'benchforge_run', 'benchforge_extend']);

/** 检查交付文件及其目录中的视图元数据，识别未附带必要采集证据的交付。 */
export async function inspectDeliveryFiles(files, cwd, signal) {
  const directories = new Set();
  const metadata = new Set();
  for (const file of files) {
    const name = path.resolve(cwd, file.path);
    if (/^view_.*\.json$/.test(path.basename(name))) metadata.add(name);
    const directory = path.dirname(name);
    const relative = path.relative(cwd, directory);
    // A file presented at workspace root is not a declaration of the whole repo.
    if (relative && !relative.startsWith('..') && !path.isAbsolute(relative)) directories.add(directory);
  }
  /** 递归查找交付目录中的视图元数据，并在每层响应取消。 */
  async function walk(directory) {
    signal.throwIfAborted();
    for (const entry of await fs.readdir(directory, { withFileTypes: true })) {
      const name = path.join(directory, entry.name);
      if (entry.isDirectory()) await walk(name);
      else if (entry.isFile() && /^view_.*\.json$/.test(entry.name)) metadata.add(name);
    }
  }
  for (const directory of directories) {
    if (![...directories].some(other => other !== directory && directory.startsWith(other + path.sep))) await walk(directory);
  }
  const issues = [];
  let views = 0;
  for (const name of [...metadata].sort()) {
    signal.throwIfAborted();
    let view;
    try { view = JSON.parse(await fs.readFile(name, { encoding: 'utf8', signal })); }
    catch (error) {
      if (!(error instanceof SyntaxError)) throw error;
      issues.push({ metadata: name, problem: 'invalid_json' }); continue;
    }
    if (!view.frame_id || typeof view.image !== 'string' || !view.image_size) continue;
    views++;
    const target = path.resolve(path.dirname(name), view.image);
    let exists;
    try { exists = (await fs.stat(target)).isFile(); }
    catch (error) { if (error.code !== 'ENOENT') throw error; exists = false; }
    if (!exists) issues.push({ metadata: name, image: view.image, target, problem: 'image_not_found' });
  }
  return { views, issues };
}

/** 在生产轮次结束时检查本轮已呈现的交付文件，发现缺少证据时向 Agent 发出修复提示。 */
export function createDeliveryFileReview({ createMessage, inspect = inspectDeliveryFiles }) {
  return /** 只复核本轮场景生产实际呈现的交付文件，缺失采集证据时注入修复通知。 */ async ({ agent, turn, signal }) => {
    signal.throwIfAborted();
    const events = agent.session.snapshotEvents();
    if (!events.some(e => e.type === 'tool/call' && sceneTools.has(e.data.name))) return;
    const deliveries = events.filter(e => e.type === 'deliverables/presented' && e.data.turn === turn);
    if (!deliveries.length) return;
    const callIds = deliveries.map(e => e.data.callId);
    const files = deliveries.flatMap(e => e.data.files);
    const result = await inspect(files, agent.session.header.cwd, signal);
    if (!result.issues.length) return;
    // Persist actual findings in ordinary session events. No hash or sidecar state.
    const same = events.some(e => e.type === 'user/message' && e.data.source?.kind === fileReviewKind
      && JSON.stringify(e.data.source.delivery_call_ids) === JSON.stringify(callIds)
      && JSON.stringify(e.data.source.issues) === JSON.stringify(result.issues));
    if (same) return;
    const text = `The BenchForge files you presented contain broken image/metadata links. Actual filesystem findings: ${JSON.stringify(result)}
Repair these delivered files now: keep the original image filenames, or update each exported view JSON to the matching renamed image. Preserve the original capture and simulator GT. Read the repaired links to verify them before claiming the delivery is usable. This requires no new render, model review or dataset.
This check only covers view image links in the presented delivery directories. It does not assess scene appearance or USD loading. For USD, binary grep/strings or Stage.Open alone cannot establish dependency completeness; use its asset resolver in the intended loading environment. If files cannot be accessed or repaired, report that concrete blocker. Unchanged findings are not repeatedly injected.`;
    agent.steer(createMessage({ content: [{ type: 'text', text }], source: {
      kind: fileReviewKind, delivery_call_ids: callIds, issues: result.issues,
    } }));
  };
}
