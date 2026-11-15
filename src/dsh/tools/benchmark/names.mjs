import names from '../../../capabilities/benchmark_design/resources/tool-names.json' with {type: 'json'};

const bareNames = new RegExp('\\b(' + Object.keys(names).join('|') + ')\\b', 'g');
export const toolName = key => names[key] || `benchforge_${key}`;
export const qualifyToolNames = text => text.replace(bareNames, key => names[key]);

/** Qualify schema prose only; names, enums, defaults and request data are opaque. */
export function qualifyDescriptions(schema) {
  if (Array.isArray(schema)) return schema.map(qualifyDescriptions);
  if (!schema || typeof schema !== 'object') return schema;
  return Object.fromEntries(Object.entries(schema).map(([key, value]) => [key,
    key === 'description' && typeof value === 'string' ? qualifyToolNames(value) :
      ['enum', 'const', 'default', 'examples'].includes(key) ? value : qualifyDescriptions(value)]));
}
