import {toolName} from './names.mjs';

/** Present CLI catalog examples using only the current Agent's callable tools. */
/** 把业务操作目录转换为当前步骤获准调用的宿主工具目录，过滤不可见操作和示例。 */
export function presentOperationCatalog(value, admitted) {
  /** 将模板目录操作映射为实际注册的完整工具名。 */
  const nameOf = operation => toolName(operation === 'catalog_templates' ? 'catalog' : operation);
  /** 只保留当前 Agent 获准调用的工具示例，并改用完整工具名作为键。 */
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
