import fs from 'node:fs/promises';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import {spawn} from 'node:child_process';

export const name = 'benchforge-tools';
export const inject = ['tools'];

export function invoke(python, workspace, configPath, tool, request, signal) {
  return new Promise((resolve, reject) => {
    const argv = ['-m', 'benchforge_core.cli', tool, '--workspace', workspace, '--input', '-'];
    if (configPath) argv.push('--config', configPath);
    const child = spawn(python, argv, {stdio:['pipe','pipe','pipe'], signal,
      windowsHide:true, env:{...process.env, PYTHONIOENCODING:'utf-8'}});
    let stdout = '', stderr = '';
    child.stdout.on('data', x => stdout += x);
    child.stderr.on('data', x => stderr += x);
    child.on('error', reject);
    child.on('close', code => {
      if (code !== 0) return reject(new Error(stderr || `BenchForge exited ${code}`));
      try {resolve(JSON.parse(stdout));} catch (error) {reject(error);}
    });
    child.stdin.on('error', reject);
    child.stdin.end(JSON.stringify(request));
  });
}

export async function apply(ctx, config) {
  const {defineTool} = await import(pathToFileURL(path.join(config.dshRoot,'packages/core/tools/lib/index.js')));
  const output = {schema:{type:'object',properties:{},additionalProperties:true},
    render:(_args,value)=>[{type:'text',text:JSON.stringify(value)}]};
  const definitions = {
    catalog:'Discover benchmark capabilities, request examples and evidence schemas. section=migration lists original skills with implemented scope and missing functionality; do not claim full migration.',
    status:'Read persisted operation results and artifact paths. Does not retry operations.',
    plan:'Save a benchmark brief. Planning and capability choices belong to you; no fixed stage sequence.',
    sam3:'Segment an image through the configured SAM3 service. Returns predictions and mask paths, never authoritative GT.',
    yoloe:'Detect requested objects through YOLOE; supports text and visual prompts. Inspect its output.',
    depthanything3:'Request Depth Anything 3 inference or query task/health. Inferred depth needs calibration before metric claims.',
    llm_local:'Call an explicitly configured local VLM for semantic annotation suggestions. Responses remain predictions; never use this as a question reviewer or GT writer.',
    habitat:'Capture real Habitat RGB, depth and agent state with the bundled collector in its configured environment.',
    libero:'Collect LIBERO observations and simulator state using demo or zero actions in its configured environment.',
    carla:'Capture CARLA views and actor metadata from a configured running server.',
    isaac:'Capture a native cuboid scene with the independent Isaac collector in a configured Isaac environment. Supports cameras, depth, labels and recorded poses.',
    evidence:'Import evidence JSONL. provenance.path references one JSON source document; selectors are JSON pointers into it. All relative paths resolve against the input JSONL directory. media contains public images only; assets retains private raw depth, labels and oracle code. Derive JSON oracle outputs from binary simulation inputs in task code.',
    build:'Build model-visible items/media plus separate authority sources and private assets, deriving answers from source JSON and reporting coverage. Never put raw depth or label files into media.',
    evaluate:'Score saved predictions with exact match; report missing answers. Does not call model APIs.'
  };
  const object={type:'object',properties:{},additionalProperties:true};
  const schemas={
    catalog:{section:{type:'string',enum:['overview','templates','migration']},query:{type:'string'},offset:{type:'integer'},limit:{type:'integer'}},
    status:{},
    plan:{objective:{type:'string',required:true},sources:{type:'array',items:{type:'string'}},target_items:{type:'integer'},notes:{type:'string'}},
    evidence:{input:{type:'string',required:true,description:'Source records JSONL with provenance and JSON-pointer selectors.'}},
    build:{evidence:{type:'string',required:true},items:{type:'string',required:true}},
    evaluate:{authority:{type:'string',required:true},predictions:{type:'string',required:true}},
    isaac:{scene_program:{type:'string',required:true},timeout_seconds:{type:'integer'}}
  };
  for(const tool of ['sam3','yoloe','depthanything3','llm_local']) schemas[tool]={
    action:{type:'string',description:'infer (default), health; DA3 also task, YOLOE visual, local VLM models.'},
    payload:{...object,description:'Native backend inference arguments. Catalog includes exact examples.'},task_id:{type:'string'},timeout_seconds:{type:'integer'}};
  for(const tool of ['habitat','libero','carla']) schemas[tool]={argv:{type:'array',items:{type:'string'},description:'Collector arguments; catalog has examples. Use --help for installed SDK collector options.'},timeout_seconds:{type:'integer'}};
  for (const [tool, description] of Object.entries(definitions)) {
    ctx.tools.register(defineTool({name:`benchforge_${tool}`, description,
      parameters:{workspace:{type:'string',required:true,description:'Absolute task workspace. Use the same workspace for related calls.'},...schemas[tool]},
      output, execute:(args,exec)=>{const {workspace,...request}=args; return invoke(config.python || 'python',workspace,config.configPath,tool,request,exec.signal);}}));
  }
  ctx.tools.register(defineTool({name:'benchforge_view_image', description:'Show a real local capture or annotation image to the model and user.',
    parameters:{path:{type:'string',required:true}},
    output:{...output,render:(_args,value)=>[{type:'image',attachment:value.image}]},
    execute:async args=>{
      const attachments=ctx.get('attachments');
      const data=await fs.readFile(args.path);
      return {image:await attachments.saveImage({data,mediaType:/\.jpe?g$/i.test(args.path)?'image/jpeg':'image/png',name:path.basename(args.path)})};
    }}));
}
