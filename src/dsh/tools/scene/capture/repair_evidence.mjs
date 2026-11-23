import {readFile} from 'node:fs/promises';
import path from 'node:path';

/** 把可信证据服务返回的图片存为 DSH 图片附件。 */
export async function saveSceneImage(attachments, imagePath, signal) {
  signal.throwIfAborted();
  const data = await readFile(imagePath, {signal});
  const name = path.basename(imagePath);
  const mediaType = /\.jpe?g$/i.test(name) ? 'image/jpeg' : 'image/png';
  const image = await attachments.saveImage({data, mediaType, name});
  signal.throwIfAborted();
  return image;
}

/** 只把带有原生实拍文件名的反馈转换为证据工具可读的引用。 */
export function repairEvidence(task) {
  const review = task.failure?.scene_review;
  if (!review || review.passed !== false) return;
  const progress = review.repair_progress;
  const current = {task_id:task.id, revision:task.revision};
  const base = progress?.next_base;
  const files = [];
  const findings = progress?.findings || [];
  const finding = findings.find(row => row.kind === 'regressed') ??
    findings.find(row => row.kind === 'uncertain') ?? findings.find(row => row.kind === 'fixed');
  /** 记录当前版本和对照版本中可精确定位的真实图片，避免重复载入。 */
  function add(ref, view, role) {
    if (!ref?.task_id || !Number.isInteger(ref.revision) || !/^view_\d+\.png$/.test(view)) return;
    const file = 'capture/' + view;
    if (!files.some(item => item.task_id === ref.task_id && item.revision === ref.revision && item.file === file))
      files.push({task_id:ref.task_id,revision:ref.revision,file,role});
  }
  add(current, finding?.after_view, '本轮实拍');
  if (finding) add(progress.baseline, finding.before_view, '对照实拍');
  for (const issue of review.issues || []) {
    if (files.length >= 2) break;
    for (const match of String(issue).matchAll(/\b(view_\d+)(?:\.png)?\b/g)) {
      add(current, match[1] + '.png', '本轮实拍');
      if (files.length >= 2) break;
    }
  }
  if (!files.length && review.renderable === true) add(current,'view_0.png','本轮实拍');
  const next = base || current;
  return {views:files, report:{...current,file:'scene_review.json'},
    ...(Number.isInteger(next.revision) && {program:{task_id:next.task_id,revision:next.revision,file:'program.json'}})};
}

/** 从原会话的授权证据接口取得问题实拍，个别读图失败时保留同批其余图片及定位。 */
export async function repairImageContent(tasks, agent, signal, {access, service}) {
  const references = tasks.flatMap(task => repairEvidence(task)?.views || []);
  if (!references.length) return [];
  const llm = agent.ctx.get('llm'), attachments = agent.ctx.get('attachments');
  const route = agent.session.requestHeader()?.config ?? {};
  const info = await llm.resolveModelInfo(route.provider ?? agent.options.provider, route.model ?? agent.options.model, signal);
  if (!info.inputModalities?.includes('image')) return [];
  const scope = access.scope(agent.session);
  const results = await Promise.allSettled(references.map(async ({role,...reference}) => {
    const item = await service('/evidence', reference, signal, {scope});
    const attachment = await saveSceneImage(attachments, item.image_path, signal);
    return [{type:'text',text:`${role} ${reference.task_id}/revision_${reference.revision}/${reference.file}`},
      {type:'image',attachment}];
  }));
  signal.throwIfAborted();
  return results.flatMap((result,index) => result.status === 'fulfilled' ? result.value :
    [{type:'text',text:`未能附上 ${references[index].file}，可用 benchforge_scene_evidence 按通知中的 task_id、revision 和 file 重读。`}]);
}
