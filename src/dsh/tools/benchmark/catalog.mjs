import {toolName} from './names.mjs';

/** Present CLI catalog examples using only the current Agent's callable tools. */
export function presentOperationCatalog(value, admitted) {
  const nameOf = operation => toolName(operation === 'catalog_templates' ? 'catalog' : operation);
  const examples = entries => Object.fromEntries(Object.entries(entries || {}).flatMap(([operation, example]) => {
    const name = nameOf(operation);
    return admitted(name) ? [[name, example]] : [];
  }));
  return {...value,
    interaction: 'Use the exact DSH tool identifiers below. Only tools admitted for this step are listed; the host supplies the current conversation workspace.',
    workflow: (value.workflow || []).flatMap(row => {
      const tools = row.tools.split(',').map(key => nameOf(key.trim())).filter(admitted);
      return tools.length ? [{...row, tools: tools.join(', ')}] : [];
    }),
    requests: examples(value.requests), production: examples(value.production),
  };
}
