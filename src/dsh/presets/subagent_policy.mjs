import Schema from '@deepseek-ai/schemastery';

export const name = 'benchforge-subagent-policy';
export const inject = ['systemPrompt', 'tools'];
export const Config = Schema.object({
  persona: Schema.string().required(),
  parentTools: Schema.array(Schema.string()).required(),
});

/** 为子 Agent 注入角色提示，并拦截只有父 Agent 才能执行的生产操作。 */
export function apply(ctx, {persona, parentTools}) {
  /** 根据原生会话来源识别子 Agent。 */
  const isSubagent = agent => agent?.session?.header?.origin === 'subagent';
  const parentOnly = new Set(parentTools);
  for (const tool of parentTools) parentOnly.add(tool.replace(/^benchforge_/, ''));
  ctx.systemPrompt.section({
    name,
    order: 500,
    /** 仅在当前作用域属于子 Agent 时提供对应角色提示。 */
    text: ({scope}) => isSubagent(scope) ? persona : '',
  });
  ctx.tools.guard(call => isSubagent(call.agent) && parentOnly.has(call.name)
    ? 'The parent agent owns this production operation.'
    : undefined);
}
