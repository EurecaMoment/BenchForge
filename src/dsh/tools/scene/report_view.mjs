// 给原生 8192 字符裁剪阈值预留结果元数据和分页指引空间。
export const DEFAULT_REPORT_MAX_CHARS=6000;
export const reportViewParameters={
  json_pointer:{type:'string',description:'Optional RFC 6901 pointer into the original JSON (evidence: relative to data), e.g. /production_acceptance/physical_issues. Read returned pointers directly; no shell parsing needed.'},
  offset:{type:'integer',description:'Zero-based array offset at json_pointer.'},
  limit:{type:'integer',description:'Array page size, 1..100; default 20.'},
  detail:{type:'boolean',description:'Return the full selected JSON instead of a bounded overview. Original reports are always retained.'},
};

/** 拆开呈现参数与原有服务请求，避免把新增的阅读选项转发给严格业务接口。 */
export function splitReportRequest(args) {
  const {json_pointer,offset,limit,detail,...request}=args;
  if(json_pointer!==undefined && (typeof json_pointer!=='string' || json_pointer!==''&&!json_pointer.startsWith('/')))
    throw new Error('json_pointer must be empty or start with /');
  if(offset!==undefined && (!Number.isInteger(offset)||offset<0))throw new Error('offset must be a nonnegative integer');
  if(limit!==undefined && (!Number.isInteger(limit)||limit<1||limit>100))throw new Error('limit must be between 1 and 100');
  return {request,selection:{json_pointer,offset,limit,detail}};
}

/** 转义 JSON 指针片段；对象名中的斜杠和波浪号仍可精确查询。 */
const childPointer=(pointer,key)=>pointer+'/'+String(key).replaceAll('~','~0').replaceAll('/','~1');

/** 仅沿自有属性读取原始报告，缺失字段返回可操作的错误，不猜测另一层包装。 */
function select(value,pointer) {
  if(!pointer)return value;
  for(const part of pointer.slice(1).split('/')) {
    if(/~(?:[^01]|$)/.test(part))throw new Error('Invalid JSON pointer escape');
    const key=part.replaceAll('~1','/').replaceAll('~0','~');
    if(value===null || typeof value!=='object' || !Object.hasOwn(value,key))
      throw new Error('JSON pointer does not exist: '+pointer);
    value=value[key];
  }
  return value;
}

/** 给大字段提供数量、准确指针和少量样例，明确标记省略，绝不改写原始证据。 */
function overview(value,pointer,depth=0) {
  if(value===null || typeof value!=='object') {
    return typeof value==='string'&&value.length>700
      ? {json_pointer:pointer,characters:value.length,preview:value.slice(0,700),truncated:true} : value;
  }
  if(Array.isArray(value))return {json_pointer:pointer,total:value.length,
    sample:depth<2?value.slice(0,3).map((row,i)=>overview(row,childPointer(pointer,i),depth+1)):[],
    next_offset:0,complete:value.length===0};
  const keys=Object.keys(value);
  if(depth>=2)return {json_pointer:pointer,keys:keys.slice(0,30),total_keys:keys.length,complete:false};
  return Object.fromEntries(keys.slice(0,30).map(key=>[key,overview(value[key],childPointer(pointer,key),depth+1)]));
}

/** 只压缩模型呈现：原始工具值、服务合同和产物仍完整，分页通过同一工具读取。 */
export function projectReport(value,args={},maxChars=DEFAULT_REPORT_MAX_CHARS) {
  const {selection}=splitReportRequest(args);
  const pointer=selection.json_pointer || '';
  const selected=select(value,pointer);
  const offset=selection.offset ?? 0,limit=selection.limit ?? 20;
  const explicit=selection.json_pointer!==undefined || selection.offset!==undefined || selection.limit!==undefined;
  if(!explicit && (selection.detail===true || JSON.stringify(selected).length<=maxChars))return selected;
  const array=Array.isArray(selected),page=array?selected.slice(offset,offset+limit):selected;
  if(!array && selection.offset!==undefined)throw new Error('offset requires an array json_pointer');
  const summarized=selection.detail!==true && JSON.stringify(page).length>maxChars;
  let data=summarized?overview(page,pointer):page,returned=array?page.length:0;
  // 分页中的样例指针必须引用原始数组的绝对索引，而非当前页内的索引。
  if(summarized&&array) {
    data=page.map((row,i)=>overview(row,childPointer(pointer,offset+i),1));
    while(data.length>1 && JSON.stringify(data).length>maxChars)data.pop();
    returned=data.length;
  }
  if(summarized && JSON.stringify(data).length>maxChars)
    data={json_pointer:pointer,keys:array?undefined:Object.keys(selected).slice(0,100),total:array?selected.length:undefined,complete:false};
  return {report_view:{json_pointer:pointer,complete:!summarized&&(!array||offset===0&&page.length===selected.length),
    summarized, ...(array?{total:selected.length,offset,returned,
      next_offset:offset+returned<selected.length?offset+returned:null}:{}),
    instruction:'Read json_pointer fields or remaining array pages with this same tool. Overview samples are incomplete; they do not establish acceptance. detail=true retrieves full selected JSON.'},data};
}
