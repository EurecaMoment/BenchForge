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

  /** 包装原生工具执行管线，在原生验证前补齐会话工作区并解析兼容名称与文件引用。 */
  constructor(ctx) {
    const native = new ToolExecutionPipeline(ctx);
    const corrections = new WeakMap();
    /** 解析当前可执行工具名，并将 BenchForge 工具的工作区参数绑定为当前会话目录。 */
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
      /** 在完成工具名和工作区绑定后查询原生执行模式。 */
      executionMode: input => native.executionMode(identity(input)),
      /** 规范化可读文件参数后调用原生准备流程，并记录需要反馈给模型的显式参数修正。 */
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
      /** 将已准备的调用交给原生管线派发，保留其策略和取消处理。 */
      dispatch: exec => native.dispatch(exec),
      /** 先完成原生工具结算，再为需要纠正的显式输入附加反馈。 */
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

  /** 投影当前工具结构；宿主负责注入工作区时，从模型参数中隐藏该字段。 */
  describe(tools) {
    return {schemas: tools.map(tool => {
      const schema = projectToolSchema(tool, false);
      if (!tool.name.startsWith('benchforge_') || schema.parameters?.properties?.workspace?.type !== 'string') return schema;
      const {workspace, ...properties} = schema.parameters.properties;
      return {...schema, parameters: {...schema.parameters, properties,
        required: (schema.parameters.required || []).filter(key => key !== 'workspace')}};
    })};
  }

  /** 沿包装后的原生管线执行工具批次，并使用统一的批次并发上限。 */
  execute(input) {
    return executeToolBatch(input, this.pipeline, () => 10);
  }
}

/** 区分模型显式输入修正与正常的宿主工作区注入，决定是否需要额外反馈。 */
function inputCorrection({requested, resolved}) {
  return requested.name !== resolved.name || Object.keys(requested.arguments).length > 0 ||
    Object.keys(resolved.arguments).some(key => key !== 'workspace');
}

/** 将 BenchForge 执行器安装到当前作用域的工具服务。 */
export function apply(ctx) {
  ctx.tools.setExecutor(new BenchForgeExecutor(ctx));
}
