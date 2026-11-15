import fs from 'node:fs/promises';
import path from 'node:path';
import {defineTool} from '@deepseek-ai/dsh-tools';
import Schema from '@deepseek-ai/schemastery';
import {launchEnvironmentOf} from '@deepseek-ai/dsh-launch-environment';
import {toolName, qualifyToolNames, qualifyDescriptions} from '../tools/benchmark/names.mjs';
import {presentOperationCatalog} from '../tools/benchmark/catalog.mjs';

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
});

export async function invoke(subprocess, python, workspace, configPath, tool, request, signal, options = {}) {
  signal?.throwIfAborted();
  const cwd = path.resolve(workspace);
  await fs.mkdir(cwd, {recursive:true});
  const env = {...options.environment, PYTHONIOENCODING:'utf-8', BENCHFORGE_MANAGED_SUBPROCESS:'1'};
  const executable = await subprocess.resolveExecutable(python, env, signal);
  signal?.throwIfAborted();
  const argv = [executable, '-m', options.module, tool, '--workspace', cwd, '--input', '-'];
  if (configPath) argv.push('--config', path.resolve(configPath));
  const handle = subprocess.spawn({argv, cwd, env, signal, graceMs:options.graceMs ?? 1000,
    stdio:{stdin:{data:JSON.stringify(request)}, stdout:{maxBytes:options.maxOutputBytes ?? 16 * 1024 * 1024}, stderr:{maxBytes:65536}}});
  options.active?.add(handle);
  try {
    const outcome = await handle.done;
    signal?.throwIfAborted();
    const stdout = handle.collected.stdout.readFrom(0);
    const stderr = handle.collected.stderr.readFrom(0);
    if (outcome.exitCode !== 0) throw new Error(stderr.text || `BenchForge exited ${outcome.exitCode ?? outcome.signal}`);
    if (stdout.lossy) throw new Error(`BenchForge JSON exceeds maxOutputBytes (${options.maxOutputBytes ?? 16 * 1024 * 1024}); increase the plugin limit for this result.`);
    return JSON.parse(stdout.text);
  } finally {
    // A CLI result is complete only after all of its owned work has stopped.
    // Explicit background jobs transfer to their own service before returning.
    handle.terminate();
    await handle.waitForExit();
    options.active?.delete(handle);
  }
}

export async function registerPythonTools(ctx, config, {definitions, schemas, module}) {
  const active = new Set();
  const lifetime = new AbortController();
  ctx.effect(() => async () => {
    lifetime.abort(new DOMException('BenchForge plugin unloaded', 'AbortError'));
    for (const handle of active) handle.terminate();
    await Promise.all([...active].map(handle => handle.waitForExit()));
  });
  const snapshot = launchEnvironmentOf(ctx);
  const environment = Object.fromEntries((config.forwardEnvironment || []).map(key => [key,snapshot.get(key)?.value]).filter(([,value])=>value!==undefined));
  Object.assign(environment,config.environment);
  if (config.serviceConfig) environment.BENCHFORGE_SERVICE_CONFIG = path.resolve(config.serviceConfig);
  const output = {schema:{type:'object',properties:{},additionalProperties:true},
    render:(_args,value)=>[{type:'text',text:JSON.stringify(value)}]};
  const operationOutput = {...output,schema:{type:'object',properties:{operation_id:{type:'string',required:true},artifact_dir:{type:'string',required:true}},additionalProperties:true}};
  const statusOutput = {...output,schema:{type:'object',properties:{operations:{type:'array',required:true,items:{type:'object',properties:{id:{type:'string',required:true},tool:{type:'string',required:true},state:{type:'string',required:true}},additionalProperties:true}}},additionalProperties:true}};
  let annotation;
  if (definitions.model_infer && config.configPath && (!config.includeTools || config.includeTools.includes('model_infer'))) {
    try {
      const settings = JSON.parse(await fs.readFile(path.resolve(config.configPath), 'utf8'));
      const llm = settings.providers?.DSpark4A100;
      annotation = {...llm, ...llm?.roles?.annotation};
    } catch (error) {
      if (error.code !== 'ENOENT') throw error;
    }
  }
  const annotationToken = annotation?.token_env;
  const credential = annotationToken ? snapshot.get(annotationToken)?.value : undefined;
  const annotationEnvironment = credential === undefined ? environment : {...environment, [annotationToken]:credential};
  for (const [tool, description] of Object.entries(definitions)) {
    if (config.includeTools && !config.includeTools.includes(tool)) continue;
    if (tool === 'model_infer' && !annotation?.url) continue;
    ctx.tools.register(defineTool({name:toolName(tool), description:qualifyToolNames(description),
      parameters:{workspace:{type:'string',required:true,description:'Task workspace bound to the current conversation by the host.'},...qualifyDescriptions(schemas[tool])},
      output:tool==='catalog'?output:tool==='status'?statusOutput:operationOutput,
      execute:async (args,exec)=>{const {workspace,...request}=args; const signal=exec.signal ? AbortSignal.any([exec.signal,lifetime.signal]) : lifetime.signal;
        if (exec.agent?.session?.header?.cwd && await fs.realpath(workspace) !== await fs.realpath(exec.agent.session.header.cwd)) {
          throw new Error('Use this conversation workspace: ' + exec.agent.session.header.cwd + '. Save task files in its tasks/ subdirectory.');
        }
        const scoped = tool === 'model_infer' ? annotationEnvironment : environment;
        const sessionEnvironment = {...scoped, BENCHFORGE_DSH_SESSION_ID: exec.agent?.session?.id || '', BENCHFORGE_DSH_CALL_ID: exec.callId || ''};
        const result = await invoke(ctx.subprocess,config.python || 'python3',workspace,config.configPath,tool,request,signal,{...config,environment:sessionEnvironment,active,module});
        return tool === 'catalog' && result.workflow
          ? presentOperationCatalog(result, name => Boolean(ctx.tools.executable(name, exec.agent, exec.toolSet)))
          : result;}}));
  }
}
