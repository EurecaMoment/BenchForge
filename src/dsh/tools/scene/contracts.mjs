// Stable response families shared by the service, with room for its evidence
// and operation-specific fields. Do not require fields an endpoint omits.
import {DEFAULT_REPORT_MAX_CHARS,projectReport} from './report_view.mjs';
import {preflightOverview} from './preflight_overview.mjs';
/** 将结构化工具结果序列化为模型可读文本。 */
const textRender=(_args,value)=>[{type:'text',text:JSON.stringify(value)}];
/** 为对象形工具结果建立宽松输出结构和统一文本渲染器。 */
const object=(properties)=>({schema:{type:'object',additionalProperties:true,properties},render:textRender});
const string={type:'string',required:true};

/** 保留准入状态及产物路径在顶层；大诊断提供明确的分页入口而不复制几何预览。 */
export function renderPreflight(args,value,maxChars=DEFAULT_REPORT_MAX_CHARS) {
  const {geometry_preview,checked,...diagnostics}=value;
  const projected=projectReport(diagnostics,args,maxChars);
  const overview=projected.report_view && args.json_pointer===undefined && args.offset===undefined && args.limit===undefined;
  if(overview && Array.isArray(diagnostics.issues))
    return textRender(args,preflightOverview(diagnostics,projected.report_view,maxChars));
  const {schema,passed,status,report_path,program_snapshot_path}=diagnostics;
  return [{type:'text',text:JSON.stringify(overview?{...projected.data,schema,passed,status,report_path,program_snapshot_path,report_view:projected.report_view}:projected)}];
}

/** 证据数据保持单层 data 包装，阅读状态与图片附件元信息始终留在顶层。 */
export function renderEvidence(args,value,maxChars=DEFAULT_REPORT_MAX_CHARS) {
  const {image,...result}=value;
  if(value.data!==undefined) {
    const projected=projectReport(value.data,args,maxChars);
    if(projected.report_view)Object.assign(result,{data:projected.data,report_view:projected.report_view});
  }
  return [{type:'text',text:JSON.stringify(result)},...(image?[{type:'image',attachment:image}]:[])];
}

export const objectOutput=object({});
export const preflightOutput = {
  ...object({
    schema: string, passed: {type:'boolean', required:true}, status: string,
    report_path: string, program_snapshot_path: string,
    issues: {type:'array', required:true, items:{type:'object', additionalProperties:true}},
    repair_groups: {type:'array', required:true, items:{type:'object', additionalProperties:true}},
    skipped_checks: {type:'array', required:true, items:{type:'object', additionalProperties:true}},
  }),
  /** 保留完整诊断与分组信息，同时从模型返回内容中省略已保存的大体积几何预览。 */
  render: renderPreflight,
};
export const runOutput=object({run_id:string,state:string});
export const taskRunOutput=object({run_id:string,task_id:string,state:string});
export const observationOutput=object({run_id:string,state:string,tasks:{type:'array',required:true,items:{type:'object',additionalProperties:true}}});
export const taskOutput=object({task_id:string});
export const cancellationOutput=object({cancel_requested:{type:'boolean',required:true}});
