import {readFile} from 'node:fs/promises';
import {qualifyToolNames} from '../tools/benchmark/names.mjs';
import {stageContract} from '../production/stages.mjs';

async function readResource(name) {
  const contents = await readFile(new URL('./resources/' + name, import.meta.url), 'utf8');
  return JSON.parse(contents);
}

export async function readTaskTemplates() {
  return readResource('task_templates.json');
}

export async function readAssessmentTemplate() {
  const template = await readResource('assessment_template.json');
  return {...template, stage_contract: stageContract,
    instructions: qualifyToolNames(template.instructions), scene_binding: qualifyToolNames(template.scene_binding)};
}
