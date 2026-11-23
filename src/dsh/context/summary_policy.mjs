import Schema from '@deepseek-ai/schemastery';

export const name = 'benchforge-summary-policy';
export const inject = ['systemPrompt'];
export const Config = Schema.object({targetTokens: Schema.number().step(1).min(1000).max(8000).default(4000)});

/** 通过预设的标准提示词入口约束检查点长度，自动与手动压缩都能从原生日志重建该要求。 */
export function apply(ctx, {targetTokens}) {
  ctx.systemPrompt.section({name, order: 550, text: () =>
    `When asked to produce a compaction checkpoint for this BenchForge conversation, aim for at most ${targetTokens} tokens. ` +
    'Keep all sections requested by the compaction engine, user hard requirements, current plan/review state, accepted artifacts, ' +
    'active job and scene revision identities, unresolved issues and the next concrete action. Keep the latest verified facts; ' +
    'distinguish observations from hypotheses. Reference saved scripts and reports by their exact paths instead of copying code, ' +
    'full tool output, obsolete plans, resolved errors or repeated reminders. Consolidate previous checkpoints, never nest or copy them. ' +
    'Preserve essential constraints even if that requires exceeding the size target. This guidance applies only to checkpoints; ' +
    'it does not limit normal answers, reasoning, image inputs or production requirements.'});
}
