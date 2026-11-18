/** Recognize the composer's explicit execution contract, never incidental task keywords. */
export function isProductionRequest(message) {
  if (message?.source?.kind !== 'user') return false;
  const text = (message.content || []).filter(block => block.type === 'text').map(block => block.text).join('\n');
  return /^execution_mode=(production|exploration)[；;]quantity_contract=(exact|minimum|best_effort)[。.]?\s*$/m.test(text)
    && /^请实际执行一次 BenchForge 评测数据集生产任务[，,]/m.test(text);
}
