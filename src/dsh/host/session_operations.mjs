import Schema from '@deepseek-ai/schemastery';
import {createExecutionTuning} from '../workbench/sessions/execution_control.mjs';
import {createSynthesisSessions} from '../workbench/sessions/synthesis.mjs';
import {defineDomain, domainTable} from '@deepseek-ai/dsh-storage-domain';
import {createUserMessage} from '@deepseek-ai/dsh-llm';
import {z} from 'zod';
import {productionProjection} from '../production/projection.mjs';
import {createProductionLifecycle} from '../production/lifecycle.mjs';
import {createStageReviewReader,reviewReadConfig} from '../production/reviews.mjs';
import {createStageReviewClient} from './stage_reviews.mjs';
import {launchEnvironmentOf} from '@deepseek-ai/dsh-launch-environment';
import {databaseEnvironment} from './database_environment.mjs';

export const name = 'benchforge-session-operations';
export const inject = ['storageDomain', 'sessionProjections', 'sessionController', 'agents', 'subprocess'];
export const Config = Schema.object({
  ...reviewReadConfig,
  revision: Schema.string().default('live-production-v1'),
  python: Schema.string().default('python3'),
  productionConfig: Schema.string().default(''),
  environment: Schema.dict(Schema.string()).default({}),
  cleanupTimeoutMs: Schema.number().min(1).default(5000),
  reviewTimeoutMs: Schema.number().min(1).default(15000),
  maxStopRecoveries: Schema.number().min(1).default(2),
});

const notices = defineDomain({name:'benchforge_production',version:1,tables:{sessions:domainTable(z.object({
  sessionId:z.string(),workspace:z.string(),plan:z.string(),run:z.string(),revision:z.number(),
  key:z.string(),suspended:z.boolean(),
  review:z.object({review_id:z.number(),stage:z.string(),artifact:z.string(),deadline:z.number(),state:z.string()}).nullable(),
}))}});

export async function apply(ctx, config) {
  const environment = {...config.environment, ...await databaseEnvironment(
    config.productionConfig, launchEnvironmentOf(ctx), config.environment)};
  const projection=await ctx.sessionProjections.register(productionProjection);
  const domain=await ctx.storageDomain.open(notices);
  const logger=ctx.logger(name);
  const reviews=createStageReviewClient(ctx.subprocess,{...config,environment,onCleanupError:error=>logger.error(error)});
  const production=createProductionLifecycle({projection,table:domain.table('sessions'),agents:ctx.agents,
    readReview:createStageReviewReader({...config,getEnvironmentValue:name=>launchEnvironmentOf(ctx).get(name)?.value}).byId,
    advanceReview:reviews.advance,createMessage:createUserMessage,onError:error=>logger.error(error),maxStopRecoveries:config.maxStopRecoveries,
    reconcileCaptures:(agent,signal)=>ctx.get('benchforgeCapture')?.continuation.reconcile(agent,{signal}),
    restoreAgent:async id=>{
      const result=await ctx.sessionController.resolveAgent(id);
      if(result.error)throw result.error;
      return result.agent;
    }});
  const execution = createExecutionTuning({ctx});
  ctx.effect(() => async () => {
    try {
      const results=await Promise.allSettled([production.dispose(),reviews.dispose()]);
      const errors=results.filter(result=>result.status==='rejected').map(result=>result.reason);
      if(errors.length)throw new AggregateError(errors,'BenchForge session operations did not fully release their processes');
    }
    finally {await domain.close();execution.dispose();}
  }, name);
  ctx.on('agent/pre-step',(payload,next)=>production.preStep(payload,next),{prepend:true});
  ctx.on('agent/turn-stopping',payload=>production.stopping(payload));
  ctx.on('agent/created',async ({agent})=>{await production.resume(agent);});
  ctx.on('ready',()=>production.start());
  for(const agent of ctx.agents.list()) await production.resume(agent);
  ctx.provide('benchforgeSessions', {
    execution,
    production,
    preparation: (workspacePath, batchSize) => createSynthesisSessions({ctx, workspacePath, batchSize}),
  });
}
