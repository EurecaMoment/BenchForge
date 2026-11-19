import fs from 'node:fs/promises';
import path from 'node:path';
import {ApiError, inside, readJson, token} from '../host/session_scope.mjs';

const names = ['办公区域', '休闲会议角', '书房茶水角'];
const mime = {'.png':'image/png', '.json':'application/json', '.jsonl':'application/x-ndjson', '.md':'text/markdown; charset=utf-8'};

/** Read a shipped archive through the same workbench response contract, without replaying production. */
/** 创建受清单约束的示例工作台读取器，并缓存已验证的示例展示数据。 */
export function createExampleWorkbench({publicPlan}) {
  const cache = new Map();
  /** 核验示例安装回执和会话标识，只加载清单允许公开的计划、数据集与媒体。 */
  async function load(root, sessionId, signal) {
    const marker = await readJson(path.join(root.path, '.benchforge-example.json'));
    if (marker?.id !== 'spatialforge-v1') return null;
    const asset = path.join(root.path, 'example', 'SpatialForge');
    const manifest = await readJson(path.join(asset, 'bootstrap.json'));
    if (manifest?.sessionId !== sessionId || !manifest.workbench) return null;
    const key = root.path + ':' + sessionId;
    if (cache.has(key)) return cache.get(key);
    /** 解析示例资源真实路径，拒绝离开安装资产目录的引用。 */
    const resolve = async file => inside({path:asset}, path.resolve(asset, file));
    /** 在取消信号约束下读取并解析已验证的示例 JSON 文件。 */
    const read = async file => JSON.parse(await fs.readFile(await resolve(file), {encoding:'utf8', signal}));
    const inventory = await read('sample-manifest.json'), config = manifest.workbench;
    const plan = publicPlan(await read(config.plan));
    const dataset = await resolve(config.dataset), evidence = await resolve(config.evidence);
    const rows = (await fs.readFile(path.join(dataset,'questions.jsonl'), 'utf8')).trim().split('\n').filter(Boolean).map(JSON.parse);
    const captures = (await fs.readFile(evidence,'utf8')).trim().split('\n').filter(Boolean).map(JSON.parse);
    if (rows.length !== inventory.question_count || captures.length !== inventory.collection_record_count)
      throw new Error('The bundled example inventory does not match its files');
    const files = [], allowed = new Set();
    /** 生成绑定当前工作区和会话的示例媒体访问链接。 */
    const link = relative => '/api/benchforge/workbench?' + new URLSearchParams({view:'example_media',
      file:relative, session_id:sessionId, workspace_path:root.path});
    /** 验证资源路径后加入公开白名单，并按扩展名登记工作台文件类型。 */
    const add = async (file, stage, title, extra={}) => {
      await resolve(file); allowed.add(file);
      files.push({stage, title, file, kind:path.extname(file)==='.png'?'image':path.extname(file)==='.jsonl'?'text':'json', url:link(file), ...extra});
    };
    await add(config.plan,'S1','原始评测计划');
    for (let i=0;i<inventory.scenes.length;i++) {
      const scene=inventory.scenes[i], id=scene.scene_id;
      const declared=scene.path.replace(/^example\/SpatialForge\//,'');
      await add(declared,'S2',names[i]+'场景程序',{task_id:id,scene_name:names[i]});
      for(let view=0;view<8;view++) {
        const file=`bench-office100/captures/${id}/${id}_view_${view}.png`;
        await add(file,'S2',names[i]+' · 机位 '+(view+1),{task_id:id,scene_name:names[i]});
      }
      for(const name of ['summary.json','objects.jsonl','cameras.jsonl'])
        await add(`bench-office100/annotations/${id}/${name}`,'S3',names[i]+' · '+name,{task_id:id});
    }
    await add(config.evidence,'S3','10 条采集与标注记录');
    const report={body_markdown:`# 公开样板数据清单\n\n此处展示仓库保留的历史产物，未在本机重新执行评测。\n\n- 场景：${inventory.scenes.length} 个，24 张采集图。\n- 采集与标注：${captures.length} 条记录。\n- 题目：${rows.length} 道，覆盖 ${inventory.question_template_count} 个模板。\n- 训练 / 开发 / 测试：${inventory.question_split.train} / ${inventory.question_split.dev} / ${inventory.question_split.test}。\n\n原始计划为 100 题；公开包保留 25 题。完整答案库、SFT 答案和最终评测评分报告未包含，不能据此宣称完成 100 题或已通过正式评测。`,
      total:rows.length, per_partition:inventory.question_split, source_scene_count:inventory.scenes.length};
    files.push({stage:'S5',kind:'json',title:'样板数据与保留范围',step_id:'example-inventory',data:report});
    const value={asset,root,manifest,plan,dataset,files,allowed,link,inventory,report};
    if(cache.size>=8)cache.delete(cache.keys().next().value);
    cache.set(key,value);return value;
  }
  return /** 仅为准确匹配的示例会话分发预置工作台视图和清单允许的媒体读取。 */ async ({roots,scope,url,request}) => {
    if(scope.history || !scope.session_id)return null;
    let example;
    for(const root of roots) {example=await load(root,scope.session_id,request.signal);if(example)break;}
    if(!example)return null;
    const {root,asset,plan,dataset,files,allowed,report}=example;
    const view=url.searchParams.get('view'), id='bundled-example', stamp=new Date().toISOString();
    if(request.method!=='GET')throw new ApiError('样板产物为只读，请在新会话中开始自己的评测。',405);
    const sample={title:'公开样板 · 历史产物预览',description:'3 个场景 · 10 条采集记录 · 25 道题。原始计划和历史对话保留；当前未重新执行。',
      stages:{S1:'previewable',S2:'previewable',S3:'previewable',S4:'previewable',S5:'previewable'}};
    let value;
    if(view==='summary')value={example:sample,runs:[{id,label:'公开样板产物',state:'previewable',steps:5,updated_at:stamp}],
      datasets:[{id:token(root,path.relative(root.path,dataset)),name:'SpatialForge · 25 道样题',run_id:id,state:'unassessed',version:'公开样板',workspace:root.title}],
      plan,plan_source:{plan_path:path.join(asset,example.manifest.workbench.plan)},sources:[],snapshot_at:stamp};
    else if(view==='progress')value={example:sample,operations:[],pipelines:[],scenes:{status:'archived',runs:[]},parallel_tasks:[],snapshot_at:stamp};
    else if(view==='run' && url.searchParams.get('run')===id)value={id,plan,state:{state:'previewable'},steps:[],scene_ids:[],stage_artifacts:files,artifacts:[],repairs:[]};
    else if(view==='stage_review')value={state:'none',can_continue:false};
    else if(view==='scene_library' || view==='scene_selection')value={items:[],selection:{selected:[],allow_new_scenes:false,version:0},total:0};
    else if(view==='run_product' && url.searchParams.get('run')===id &&
      (url.searchParams.get('product')==='plan' || url.searchParams.get('product')==='report' && url.searchParams.get('step')==='example-inventory'))
      value=url.searchParams.get('product')==='plan'?plan:report;
    else if(view==='example_media') {
      const file=url.searchParams.get('file');
      if(!allowed.has(file))throw new ApiError('This file is not a declared example artifact',403);
      const resolved=await inside({path:asset},path.resolve(asset,file));
      return {value:new Response(await fs.readFile(resolved,{signal:request.signal}),{headers:{
        'content-type':mime[path.extname(file)]||'application/octet-stream','cache-control':'private, max-age=60','x-content-type-options':'nosniff'}})};
    } else return null;
    return {value};
  };
}
