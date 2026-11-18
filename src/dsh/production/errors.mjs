import {HarnessError} from '@deepseek-ai/dsh-error';

export class ProductionIncompleteError extends HarnessError {
  constructor() {
    super('BenchForge 任务尚未完成，模型连续结束且没有推进到可交付或明确受阻的状态。已保留任务和产物，请查看实际缺项后继续。',
      'BENCHFORGE_PRODUCTION_INCOMPLETE');
  }
}
