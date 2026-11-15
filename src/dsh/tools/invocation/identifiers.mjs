import {toolName} from '../benchmark/names.mjs';
import names from '../../../capabilities/benchmark_design/resources/tool-names.json' with {type: 'json'};

/** Exact compatibility spellings only. The registry still decides admission. */
export function canonicalToolName(name) {
  if (Object.hasOwn(names, name)) return toolName(name);
  return name.replace(/^benchmarkforge_/, 'benchforge_');
}

export function resolveToolName(tools, input) {
  const {name, agent, toolSet} = input;
  if (tools.executable(name, agent, toolSet)) return name;
  const canonical = canonicalToolName(name);
  return canonical !== name && tools.executable(canonical, agent, toolSet) ? canonical : name;
}
