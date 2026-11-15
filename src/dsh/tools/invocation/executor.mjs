import {projectToolSchema} from '@deepseek-ai/dsh-tools';
import {ToolExecutionPipeline, executeToolBatch} from '@deepseek-ai/dsh-tools/pipeline';
import {createUserMessage} from '@deepseek-ai/dsh-llm';
import {resolveToolName} from './identifiers.mjs';
import {resolveReadArguments} from './workspace.mjs';

export const name = 'benchforge-invocation';
export const inject = ['tools'];

/** Normalize references before native validation, policy, scheduling and dispatch. */
export class BenchForgeExecutor {
  sections = [{name: 'benchforge-invocation', order: 510, text:
    'Use tool names exactly as declared in the current tool schemas. Internal operation IDs are not tool names. ' +
    'BenchForge workspace parameters are bound by the host to this conversation; omit them. ' +
    'For read_image/read, prefer paths relative to the current working directory (tasks/... or operations/...). ' +
    'Copy filenames from actual tool receipts or listings. Never reconstruct a long home/repository prefix or invent an image filename.'}];

  constructor(ctx) {
    const native = new ToolExecutionPipeline(ctx);
    const corrections = new WeakMap();
    const identity = input => {
      const name = resolveToolName(ctx.tools, input);
      const tool = ctx.tools.executable(name, input.agent, input.toolSet);
      const cwd = input.agent?.session?.header?.cwd, args = input.arguments;
      const bound = name.startsWith('benchforge_') && tool?.parameters?.properties?.workspace?.type === 'string' &&
        cwd && args && typeof args === 'object' && !Array.isArray(args) &&
        (args.workspace === undefined || typeof args.workspace === 'string');
      return {...input, name, arguments: bound && args.workspace !== cwd ? {...args, workspace: cwd} : args};
    };
    this.pipeline = {
      executionMode: input => native.executionMode(identity(input)),
      prepare: async input => {
        const resolved = identity(input);
        if (!input.signal.aborted && ctx.tools.executable(resolved.name, input.agent, input.toolSet)) {
          try {
            resolved.arguments = await resolveReadArguments(resolved.name, resolved.arguments,
              input.agent?.session?.header?.cwd, input.signal);
          } catch {
            // Keep native cancellation and filesystem diagnostics authoritative;
            // reference inspection must not fail an otherwise settleable batch.
          }
        }
        const prepared = await native.prepare(resolved);
        if (resolved.name !== input.name || resolved.arguments !== input.arguments) {
          const changed = Object.keys(resolved.arguments || {}).filter(key =>
            resolved.arguments[key] !== input.arguments?.[key]);
          corrections.set(prepared.exec, {
            requested: {name: input.name, arguments: Object.fromEntries(changed
              .filter(key => input.arguments?.[key] !== undefined).map(key => [key, input.arguments[key]]))},
            resolved: {name: resolved.name, arguments: Object.fromEntries(changed.map(key => [key, resolved.arguments[key]]))},
          });
        }
        return prepared;
      },
      dispatch: exec => native.dispatch(exec),
      complete: async (exec, outcome) => {
        const result = await native.complete(exec, outcome);
        const correction = corrections.get(exec);
        if (!correction) return result;
        corrections.delete(exec);
        const text = 'BenchForge resolved this invocation before native validation: ' +
          JSON.stringify(correction) +
          '. Use the registered tool spelling, omit workspace, and prefer workspace-relative image paths.';
        // An omitted context-owned workspace is routine and needs no extra model
        // message. Explicit misspellings receive feedback after the real result.
        const feedback = inputCorrection(correction);
        const notice = createUserMessage({source: {kind: 'benchforge/invocation', form: 'notice'},
          content: [{type: 'text', text}]});
        return {...result, meta: {...result.meta, benchforgeInvocation: correction},
          additionalContexts: [...result.additionalContexts || [], ...(feedback ? [notice] : [])]};
      },
    };
  }

  describe(tools) {
    return {schemas: tools.map(tool => {
      const schema = projectToolSchema(tool, false);
      if (!tool.name.startsWith('benchforge_') || schema.parameters?.properties?.workspace?.type !== 'string') return schema;
      const {workspace, ...properties} = schema.parameters.properties;
      return {...schema, parameters: {...schema.parameters, properties,
        required: (schema.parameters.required || []).filter(key => key !== 'workspace')}};
    })};
  }

  execute(input) {
    return executeToolBatch(input, this.pipeline, () => 10);
  }
}

function inputCorrection({requested, resolved}) {
  return requested.name !== resolved.name || Object.keys(requested.arguments).length > 0 ||
    Object.keys(resolved.arguments).some(key => key !== 'workspace');
}

export function apply(ctx) {
  ctx.tools.setExecutor(new BenchForgeExecutor(ctx));
}
