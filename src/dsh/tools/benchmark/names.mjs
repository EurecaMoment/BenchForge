import names from '../../../capabilities/benchmark_design/resources/tool-names.json' with {type: 'json'};

const bareNames = new RegExp('\\b(' + Object.keys(names).join('|') + ')\\b', 'g');
/** 为未限定的业务工具名补上 BenchForge 前缀。 */
export const toolName = key => names[key] || `benchforge_${key}`;
/** 将说明文本中的已知业务工具名替换为宿主注册的完整名称。 */
export const qualifyToolNames = text => text.replace(bareNames, key => names[key]);

/** Qualify schema prose only; names, enums, defaults and request data are opaque. */
/** 逐项处理工具参数说明，使嵌入的业务工具引用使用完整注册名称。 */
export function qualifyDescriptions(schema) {
  if (Array.isArray(schema)) return schema.map(qualifyDescriptions);
  if (!schema || typeof schema !== 'object') return schema;
  return Object.fromEntries(Object.entries(schema).map(([key, value]) => [key,
    key === 'description' && typeof value === 'string' ? qualifyToolNames(value) :
      ['enum', 'const', 'default', 'examples'].includes(key) ? value : qualifyDescriptions(value)]));
}
