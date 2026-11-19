import Schema from '@deepseek-ai/schemastery';
import {setTimeout as delay} from 'node:timers/promises';
import {HarnessError} from '@deepseek-ai/dsh-error';
import {createServiceClient, loadServiceConfig} from '../tools/scene/service_client.mjs';
import {waitWithSignal} from '../runtime/cancellation.mjs';

export const reviewReadConfig = {
  productionConfig: Schema.string().default(''),
  reviewReadTimeoutMs: Schema.number().min(1).default(10000),
};

/** Only committed backend state may authorize production. No local DB or approval cache. */
/** 创建有超时和调用取消约束的复核只读接口，复用服务客户端并允许失败后重连。 */
export function createStageReviewReader({productionConfig, reviewReadTimeoutMs=10000,
  service, getEnvironmentValue}={}) {
  let client;
  /** 调用服务读取指定或最新复核，校验返回状态并传播取消及读取错误。 */
  async function read(workspace, sessionId, reviewId, _now, callerSignal) {
    const signal=AbortSignal.any([AbortSignal.timeout(reviewReadTimeoutMs),...(callerSignal?[callerSignal]:[])]);
    signal.throwIfAborted();
    try {
      if (!client) client=service ? Promise.resolve(service) : loadServiceConfig(productionConfig, {getEnvironmentValue})
        .then(createServiceClient).catch(error=>{client=undefined;throw error;});
      const request=await waitWithSignal(client,signal);
      const args={workspace, session:sessionId, ...(reviewId===undefined?{}:{review_id:reviewId})};
      let value;
      for (let backoff=200;;backoff=Math.min(1500,backoff*2)) {
        try {
          value=await waitWithSignal(request('/stage-review/read',args,signal),signal);
          break;
        } catch (error) {
          signal.throwIfAborted();
          if (!['ECONNREFUSED','ECONNRESET','UND_ERR_SOCKET'].includes(error.cause?.code ?? error.code)
              && ![502,503,504].includes(error.status)) throw error;
          await delay(backoff,undefined,{signal});
        }
      }
      signal.throwIfAborted();
      if (value?.state==='none') return null;
      if (!value || typeof value.state!=='string' || !Number.isInteger(value.review_id))
        throw new Error('Invalid stage review response');
      return value;
    } catch(error) {
      signal.throwIfAborted();
      throw new HarnessError('The BenchForge backend could not return the current stage review.',
        'BENCHFORGE_REVIEW_STORAGE_UNAVAILABLE',{cause:error});
    }
  }
  return {
    /** 读取该工作区和会话当前最新的阶段复核。 */
    latest:(workspace,sessionId,now=Date.now(),signal)=>read(workspace,sessionId,undefined,now,signal),
    /** 按复核标识读取指定快照，保留调用方的取消信号。 */
    byId:(workspace,sessionId,reviewId,now=Date.now(),signal)=>read(workspace,sessionId,reviewId,now,signal),
  };
}
