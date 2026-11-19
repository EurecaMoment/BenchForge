import {readFile} from 'node:fs/promises';
import {qualifyToolNames} from '../tools/benchmark/names.mjs';
import {stageContract} from '../production/stages.mjs';

/** 读取随模块发布的 JSON 资源，按模块位置解析路径。 */
async function readResource(name) {
  const contents = await readFile(new URL('./resources/' + name, import.meta.url), 'utf8');
  return JSON.parse(contents);
}

/** 加载工作台使用的静态任务模板目录。 */
export async function readTaskTemplates() {
  return readResource('task_templates.json');
}

/** 加载评测计划模板，补充阶段契约并规范其中的工具名称引用。 */
export async function readAssessmentTemplate() {
  const template = await readResource('assessment_template.json');
  return {...template, stage_contract: stageContract,
    instructions: qualifyToolNames(template.instructions), scene_binding: qualifyToolNames(template.scene_binding)};
}
