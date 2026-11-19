import {toolName} from '../benchmark/names.mjs';
import names from '../../../capabilities/benchmark_design/resources/tool-names.json' with {type: 'json'};

/** Exact compatibility spellings only. The registry still decides admission. */
/** 统一移除可选的 BenchForge 前缀，供业务操作匹配使用。 */
export function canonicalToolName(name) {
  if (Object.hasOwn(names, name)) return toolName(name);
  return name.replace(/^benchmarkforge_/, 'benchforge_');
}

/** 在当前 Agent 可见工具中解析请求名称，兼容限定名称和已有原生名称。 */
export function resolveToolName(tools, input) {
  const {name, agent, toolSet} = input;
  if (tools.executable(name, agent, toolSet)) return name;
  const canonical = canonicalToolName(name);
  return canonical !== name && tools.executable(canonical, agent, toolSet) ? canonical : name;
}
