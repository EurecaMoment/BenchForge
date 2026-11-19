import fs from 'node:fs/promises';
import path from 'node:path';
import {defineTool} from '@deepseek-ai/dsh-tools';
import Schema from '@deepseek-ai/schemastery';
import {launchEnvironmentOf} from '@deepseek-ai/dsh-launch-environment';
import {toolName, qualifyToolNames, qualifyDescriptions} from '../tools/benchmark/names.mjs';
import {configuredProviders, modelRole, providerEnvironment} from './tool_credentials.mjs';
import {presentOperationCatalog} from '../tools/benchmark/catalog.mjs';
import {waitWithSignal} from '../runtime/cancellation.mjs';
import {releaseProcess, releaseProcesses} from './process_cleanup.mjs';
import {databaseEnvironment} from './database_environment.mjs';
import {currentAccessScope} from '../access/context.mjs';
import {loadServiceConfig, signAccessScope} from '../tools/scene/service_client.mjs';
import {pythonHostDirectory, pythonHostEnvironment} from './python_environment.mjs';

export const inject = ['tools', 'attachments', 'subprocess'];
export const Config = Schema.object({
  python: Schema.string().default('python3'),
  configPath: Schema.string(),
  serviceConfig: Schema.string(),
  dshRoot: Schema.string(),
  includeTools: Schema.array(Schema.string()),
  environment: Schema.dict(Schema.string()).default({}),
  forwardEnvironment: Schema.array(Schema.string()).default([]),
  maxOutputBytes: Schema.number().min(1024).default(16 * 1024 * 1024),
  graceMs: Schema.number().min(1).default(1000),
  cleanupTimeoutMs: Schema.number().min(1).default(5000),
});

/** 通过宿主进程服务执行 Python 工具，传入工作区及请求并确保进程最终被确认回收。 */
export async function invoke(subprocess, python, workspace, configPath, tool, request, signal, options = {}) {
  signal?.throwIfAborted();
  const cwd = path.resolve(workspace);
  await fs.mkdir(cwd, {recursive:true});
  const env = pythonHostEnvironment({...options.environment, BENCHFORGE_MANAGED_SUBPROCESS:'1'});
  const executable = await subprocess.resolveExecutable(python, env, signal);
  signal?.throwIfAborted();
  const argv = [executable, '-m', options.module, tool, '--workspace', cwd, '--input', '-'];
  if (configPath) argv.push('--config', path.resolve(configPath));
  const handle = subprocess.spawn({argv, cwd: pythonHostDirectory, env, signal, graceMs:options.graceMs ?? 1000,
    stdio:{stdin:{data:JSON.stringify(request)}, stdout:{maxBytes:options.maxOutputBytes ?? 16 * 1024 * 1024}, stderr:{maxBytes:65536}}});
  options.active?.add(handle);
  try {
    const outcome = await waitWithSignal(handle.done, signal);
    signal?.throwIfAborted();
    const stdout = handle.collected.stdout.readFrom(0);
    const stderr = handle.collected.stderr.readFrom(0);
    if (outcome.exitCode !== 0) throw new Error(stderr.text || `BenchForge exited ${outcome.exitCode ?? outcome.signal}`);
    if (stdout.lossy) throw new Error(`BenchForge JSON exceeds maxOutputBytes (${options.maxOutputBytes ?? 16 * 1024 * 1024}); increase the plugin limit for this result.`);
    return JSON.parse(stdout.text);
  } finally {
    // A CLI result is complete only after all of its owned work has stopped.
    // Explicit background jobs transfer to their own service before returning.
    try {
      await releaseProcess(handle, {active: options.active, timeoutMs: options.cleanupTimeoutMs});
    } catch (error) {
      options.onCleanupError?.(error);
      if (!signal?.aborted) throw error;
    }
    signal?.throwIfAborted();
  }
}

/** 注册 Python 工具定义、可信环境和会话访问凭据，并把取消及进程清理绑定到插件寿命。 */
export async function registerPythonTools(ctx, config, {definitions, schemas, module}) {
  const active = new Set();
  const lifetime = new AbortController();
  ctx.effect(() => async () => {
    lifetime.abort(new DOMException('BenchForge plugin unloaded', 'AbortError'));
    await releaseProcesses(active, config.cleanupTimeoutMs);
  });
  const snapshot = launchEnvironmentOf(ctx);
  const environment = Object.fromEntries((config.forwardEnvironment || []).map(key => [key,snapshot.get(key)?.value]).filter(([,value])=>value!==undefined));
  Object.assign(environment,config.environment);
  Object.assign(environment,await databaseEnvironment(config.serviceConfig,snapshot,environment));
  const accessService = config.serviceConfig ? await loadServiceConfig(config.serviceConfig, {
    /** 优先读取已配置的工具环境，再回退到宿主启动环境快照。 */
    getEnvironmentValue: key => environment[key] ?? snapshot.get(key)?.value,
  }) : null;
  if (config.serviceConfig) environment.BENCHFORGE_SERVICE_CONFIG = path.resolve(config.serviceConfig);
  const output = {schema:{type:'object',properties:{},additionalProperties:true},
    /** 将工具返回值序列化为模型可读取的文本结果。 */
    render:(_args,value)=>[{type:'text',text:JSON.stringify(value)}]};
  const operationOutput = {...output,schema:{type:'object',properties:{operation_id:{type:'string',required:true},artifact_dir:{type:'string',required:true}},additionalProperties:true}};
  const statusOutput = {...output,schema:{type:'object',properties:{operations:{type:'array',required:true,items:{type:'object',properties:{id:{type:'string',required:true},tool:{type:'string',required:true},state:{type:'string',required:true}},additionalProperties:true}}},additionalProperties:true}};
  const providers = await configuredProviders(config.configPath);
  const annotation = modelRole(providers, 'annotation');
  const annotationEnvironment = providerEnvironment(environment, snapshot, annotation?.token_env);
  const isaacEnvironment = providerEnvironment(environment, snapshot, providers.IsaacSim4A100?.grpc?.token_env);
  for (const [tool, description] of Object.entries(definitions)) {
    if (config.includeTools && !config.includeTools.includes(tool)) continue;
    if (tool === 'model_infer' && !annotation?.url) continue;
    ctx.tools.register(defineTool({name:toolName(tool), description:qualifyToolNames(description),
      parameters:{workspace:{type:'string',required:true,description:'Task workspace bound to the current conversation by the host.'},...qualifyDescriptions(schemas[tool])},
      output:tool==='catalog'?output:tool==='status'?statusOutput:operationOutput,
      /** 校验会话工作区并选择对应服务环境，以当前可信访问作用域调用 Python 工具。 */
      execute:async (args,exec)=>{const {workspace,...request}=args; const signal=exec.signal ? AbortSignal.any([exec.signal,lifetime.signal]) : lifetime.signal;
        if (exec.agent?.session?.header?.cwd && await fs.realpath(workspace) !== await fs.realpath(exec.agent.session.header.cwd)) {
          throw new Error('Use this conversation workspace: ' + exec.agent.session.header.cwd + '. Save task files in its tasks/ subdirectory.');
        }
        const scoped = tool === 'model_infer' ? annotationEnvironment : tool === 'isaac_sim' ? isaacEnvironment : environment;
        const sessionEnvironment = {...scoped, BENCHFORGE_DSH_SESSION_ID: exec.agent?.session?.id || '', BENCHFORGE_DSH_CALL_ID: exec.callId || ''};
        const access = currentAccessScope();
        if (access) {
          if (!accessService) throw new Error('Workspace access requires the configured BenchForge service');
          sessionEnvironment.BENCHFORGE_ACCESS_TICKET = signAccessScope(access, accessService.operatorToken);
          sessionEnvironment.BENCHFORGE_ACCESS_KEY = accessService.operatorToken;
        }
        const result = await invoke(ctx.subprocess,config.python || 'python3',workspace,config.configPath,tool,request,signal,{...config,environment:sessionEnvironment,active,module,
          /** 将子进程回收异常写入专用宿主日志。 */
          onCleanupError: error => ctx.logger('benchforge-process-cleanup').error(error)});
        return tool === 'catalog' && result.workflow
          ? presentOperationCatalog(result, name => Boolean(ctx.tools.executable(name, exec.agent, exec.toolSet)))
          : result;}}));
  }
}
