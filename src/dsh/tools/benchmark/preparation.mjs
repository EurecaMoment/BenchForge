import fs from 'node:fs/promises';
import path from 'node:path';

/** 判断计划或会话目录是否位于显式工作区之内。 */
const inside = (root, value) => {
  const relative = path.relative(root, value);
  return relative === '' || relative !== '..' && !relative.startsWith('..' + path.sep) && !path.isAbsolute(relative);
};
/** 读取准备记录的 JSON 内容；文件尚不存在时返回空值。 */
async function readJson(file) {
  try { return JSON.parse(await fs.readFile(file, 'utf8')); }
  catch (error) { if (error.code === 'ENOENT') return null; throw error; }
}

// Native tools supply exec.agent; a model cannot select another parent identity.
// The saved plan uses the same conversation association as workbench edits.
/** 核验准备计划和父 Agent 的真实工作区、会话归属及源排除策略，返回可用于委派的绑定。 */
export async function savedPreparationPlan(workspace, planPath, agent, signal) {
  signal?.throwIfAborted();
  if (!agent || agent.id !== agent.session?.header?.id) throw new Error('Preparation needs the current parent Agent');
  const root = await fs.realpath(workspace);
  const cwd = await fs.realpath(agent.session.header.cwd);
  const file = await fs.realpath(path.resolve(root, planPath));
  if (!inside(root, cwd) || !inside(root, file) || !(await fs.stat(file)).isFile())
    throw new Error('Preparation plan and parent must belong to the named workspace');
  const policy = await readJson(path.join(root, 'scene-library', 'policy.json'));
  if (policy?.enabled && (policy.excluded_paths || []).some(folder => inside(folder, file)))
    throw new Error('This saved plan references a removed scene; use the revised parent plan');
  let current = path.dirname(file);
  while (inside(root, current)) {
    signal?.throwIfAborted();
    const records = await Promise.all(['request.json', 'state.json'].map(name => readJson(path.join(current, name))));
    if (records.some(record => record?.context?.session_id === agent.id || record?.context?.session_ids?.includes(agent.id)))
      return {workspacePath: root, planPath: file, parentId: agent.id};
    if (current === root) break;
    current = path.dirname(current);
  }
  throw new Error('The saved plan is not associated with the current parent conversation');
}

/** 用已验证的父计划启动准备工作，或从父会话自己的批次领取准备稿。 */
export function createPreparationHandler(preparation) {
  return async (args, {agent, signal}) => {
    if (args.action === 'status') {
      const header = agent?.session?.header;
      if (!header?.cwd || !agent.id || agent.id !== header.id) throw new Error('Preparation needs the current parent Agent');
      return preparation(header.cwd).read({parentId: agent.id, batchKey: args.batch_key}, signal);
    }
    if (!args.workspace || !args.plan_path || !args.tasks?.length) {
      throw new Error('Starting preparation requires workspace, plan_path and tasks');
    }
    const scope = await savedPreparationPlan(args.workspace, args.plan_path, agent, signal);
    return preparation(scope.workspacePath).start({
      parentId: scope.parentId, planPath: scope.planPath, batchKey: args.batch_key, tasks: args.tasks,
    }, signal);
  };
}
