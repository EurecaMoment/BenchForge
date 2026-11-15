import names from '../../../capabilities/benchmark_design/resources/tool-names.json' with {type: 'json'};

const bareNames = new RegExp('\\b(' + Object.keys(names).join('|') + ')\\b', 'g');
export const toolName = key => names[key] || `benchforge_${key}`;
export const qualifyToolNames = text => text.replace(bareNames, key => names[key]);
