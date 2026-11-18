import Schema from '@deepseek-ai/schemastery';
import {HarnessError} from '@deepseek-ai/dsh-error';
import {createServiceClient, loadServiceConfig} from '../tools/scene/service_client.mjs';
import {waitWithSignal} from '../runtime/cancellation.mjs';

export const reviewReadConfig = {
  productionConfig: Schema.string().default(''),
  reviewReadTimeoutMs: Schema.number().min(1).default(10000),
};

/** Only committed backend state may authorize production. No local DB or approval cache. */
export function createStageReviewReader({productionConfig, reviewReadTimeoutMs=10000,
  service, getEnvironmentValue}={}) {
  let client;
  async function read(workspace, sessionId, reviewId, _now, callerSignal) {
    const signal=AbortSignal.any([AbortSignal.timeout(reviewReadTimeoutMs),...(callerSignal?[callerSignal]:[])]);
    signal.throwIfAborted();
    try {
      if (!client) client=service ? Promise.resolve(service) : loadServiceConfig(productionConfig, {getEnvironmentValue})
        .then(createServiceClient).catch(error=>{client=undefined;throw error;});
      const request=await waitWithSignal(client,signal);
      const value=await waitWithSignal(request('/stage-review/read', {
        workspace, session:sessionId, ...(reviewId===undefined?{}:{review_id:reviewId}),
      },signal),signal);
      signal.throwIfAborted();
      if (value?.state==='none') return null;
      if (!value || typeof value.state!=='string' || !Number.isInteger(value.review_id))
        throw new Error('Invalid stage review response');
      return value;
    } catch(error) {
      signal.throwIfAborted();
      throw new HarnessError('The stage review could not be read from PostgreSQL. Production remains stopped until the backend is available.',
        'BENCHFORGE_REVIEW_STORAGE_UNAVAILABLE',{cause:error});
    }
  }
  return {
    latest:(workspace,sessionId,now=Date.now(),signal)=>read(workspace,sessionId,undefined,now,signal),
    byId:(workspace,sessionId,reviewId,now=Date.now(),signal)=>read(workspace,sessionId,reviewId,now,signal),
  };
}
