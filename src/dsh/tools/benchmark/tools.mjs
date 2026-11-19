import {Config, inject as pythonServices, registerPythonTools} from '../../host/python_tools.mjs';
import {defineTool} from '@deepseek-ai/dsh-tools';
import {createUserMessage} from '@deepseek-ai/dsh-llm';
import {definitions, schemas} from './contracts.mjs';
import {createPreparationHandler} from './preparation.mjs';
import {createDeliveryFileReview} from './delivery/delivery_files.mjs';

export {Config};
export const inject = [...pythonServices, 'benchforgeSessions'];
export const name = 'benchmark-workflows-tools';

/** 注册评测生产 Python 工具、原生准备委派入口和轮次结束时的交付文件复核。 */
export function apply(ctx, config) {
  registerPythonTools(ctx, config, {definitions, schemas, module: 'server.cli'});
  const output = {schema: {type: 'object', additionalProperties: true},
    /** 将准备操作的结构化结果转换为文本工具输出。 */
    render: (_args, value) => [{type: 'text', text: JSON.stringify(value)}]};
  ctx.on('agent/turn-stopping',createDeliveryFileReview({createMessage:createUserMessage}));
  ctx.tools.register(defineTool({name:'benchforge_prepare',description:'Prepare independent scene designs, annotation scripts or question shards in native continuable subagents. Binds the current parent and its saved plan, resolves the actual supported reasoning effort, preserves the model route and returns durable startup receipts. Create distinct output directories first. Workers only prepare; the parent merges, captures, verifies and opens the single user window for each completed stage. Startup acceptance is not a completed artifact.',parameters:{
    workspace:{type:'string',required:true,description:'Absolute shared workspace containing the parent plan and worker directories.'},
    plan_path:{type:'string',required:true,description:'Saved plan associated with the current parent conversation.'},
    batch_key:{type:'string',required:true,description:'Stable identity for this exact preparation batch; reuse on retry.'},
    tasks:{type:'array',required:true,items:{type:'object',additionalProperties:false,properties:{
      key:{type:'string',required:true},label:{type:'string',required:true},
      prompt:{type:'string',required:true,description:'Self-contained goal, frozen inputs, requirements and deliverables.'},
      outputDirectory:{type:'string',required:true,description:'Existing parent handoff directory. The child writes in its own workspace; the Host copies its declared deliverables here.'},
      inputFiles:{type:'array',items:{type:'string'},description:'Parent workspace files the Host snapshots as read-only inputs for this child.'},
    }}},
  },output,execute:createPreparationHandler(workspace =>
    ctx.benchforgeSessions.preparation(workspace, 3))}));
}
