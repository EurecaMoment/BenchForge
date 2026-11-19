import assert from 'node:assert/strict';
import test from 'node:test';
import {definitions, schemas} from './contracts.mjs';
import {qualifyToolNames, qualifyDescriptions, toolName} from './names.mjs';
import {createWorkbenchState} from '../../../ui/workbench/state.js';
import {createPrimitives} from '../../../ui/workbench/primitives.js';
import {presentOperationCatalog} from './catalog.mjs';

test('all tool and parameter descriptions use public tool identifiers', /** 验证BenchForge 工具与宿主集成在该用例中的具体边界，完成准备、调用和结果断言。用例：all tool and parameter descriptions use public tool identifiers。 */ () => {
  const bare = /\b(?:pipeline_catalog|pipeline_plan|pipeline_run|pipeline_status|pipeline_artifacts|pipeline_repair_plan|pipeline_evaluate|stage_review)\b/;
  for (const text of Object.values(definitions)) assert.doesNotMatch(qualifyToolNames(text), bare);
  /** 递归检查工具结构中的说明文字，断言业务工具引用均已限定名称。 */
  const visit = node => {
    if (!node || typeof node !== 'object') return;
    if (typeof node.description === 'string') assert.doesNotMatch(node.description, bare);
    Object.values(node).forEach(visit);
  };
  visit(qualifyDescriptions(schemas));
});

test('schema qualification leaves values and example paths unchanged', /** 验证BenchForge 工具与宿主集成在该用例中的具体边界，完成准备、调用和结果断言。用例：schema qualification leaves values and example paths unchanged。 */ () => {
  const input = {description: 'Use pipeline_catalog', enum: ['pipeline_run'],
    examples: [{description: 'pipeline_plan', path: '/work/pipeline_run/image.png'}],
    properties: {field: {type: 'string', description: 'Use pipeline_plan'}}};
  const output = qualifyDescriptions(input);
  assert.equal(output.description, 'Use ' + toolName('pipeline_catalog'));
  assert.deepEqual(output.examples, input.examples);
  assert.deepEqual(output.enum, input.enum);
  assert.equal(input.description, 'Use pipeline_catalog');
});

test('DSH catalog names only admitted tools and preserves example data', /** 验证BenchForge 工具与宿主集成在该用例中的具体边界，完成准备、调用和结果断言。用例：DSH catalog names only admitted tools and preserves example data。 */ () => {
  const value = {workflow: [{area: 'production', tools: 'pipeline_plan, pipeline_run'}, {area: 'other', tools: 'model_eval'}],
    requests: {pipeline_plan: {path: '/work/pipeline_run/input.json'}, model_eval: {model: 'offline'}},
    production: {pipeline_plan: {description: 'Plan'}}, evidence_record: {id: 'pipeline_plan'}};
  const result = presentOperationCatalog(value, name => name === 'benchforge_pipeline_plan');
  assert.deepEqual(result.workflow, [{area: 'production', tools: 'benchforge_pipeline_plan'}]);
  assert.deepEqual(Object.keys(result.requests), ['benchforge_pipeline_plan']);
  assert.equal(result.requests.benchforge_pipeline_plan.path, '/work/pipeline_run/input.json');
  assert.deepEqual(result.evidence_record, value.evidence_record);
  assert.ok(value.requests.pipeline_plan);
});

test('workbench instructions qualify tools without rewriting selected files or user data', /** 验证BenchForge 工具与宿主集成在该用例中的具体边界，完成准备、调用和结果断言。用例：workbench instructions qualify tools without rewriting selected files or user data。 */ () => {
  const path = '/work/operations/job/pipeline_run/scene.json';
  const state = createWorkbenchState({});
  const text = state.productionContext({selection_path: path, sources: [{path}], allow_new_scenes: false}, {
    instructions: 'Use pipeline_catalog', scene_binding: 'pipeline_plan.inputs.scene_selection',
    sections: {}, assessment: {title: 'pipeline_plan', file: path},
  });
  assert.ok(text.includes(path));
  assert.ok(text.includes('"title": "pipeline_plan"'));
  assert.ok(text.includes('benchforge_pipeline_catalog'));
  assert.ok(text.includes('benchforge_pipeline_plan.inputs.scene_selection'));
  assert.ok(!text.includes('/benchforge_pipeline_run/'));
  const {planEditInstruction} = createPrimitives({React: {}});
  const edit = planEditInstruction(path);
  assert.ok(edit.includes(path));
  assert.ok(edit.includes('benchforge_pipeline_plan(request_path='));
});
