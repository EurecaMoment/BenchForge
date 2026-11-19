import {HarnessError} from '@deepseek-ai/dsh-error';

export class ProductionIncompleteError extends HarnessError {
  /** 标记生产任务连续结束但尚未完成，保留已有任务和产物供后续修复。 */
  constructor() {
    super('BenchForge 任务尚未完成，模型连续结束且没有推进到可交付或明确受阻的状态。已保留任务和产物，请查看实际缺项后继续。',
      'BENCHFORGE_PRODUCTION_INCOMPLETE');
  }
}
