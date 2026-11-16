import Schema from '@deepseek-ai/schemastery';
import {createExecutionTuning} from '../workbench/sessions/execution_control.mjs';
import {createSynthesisSessions} from '../workbench/sessions/synthesis.mjs';
import {defineDomain, domainTable} from '@deepseek-ai/dsh-storage-domain';
import {createUserMessage} from '@deepseek-ai/dsh-llm';
import {z} from 'zod';
import {productionProjection} from '../production/projection.mjs';
import {createProductionLifecycle} from '../production/lifecycle.mjs';
import {readStageReview} from '../production/reviews.mjs';
import {createStageReviewClient} from './stage_reviews.mjs';

export const name = 'benchforge-session-operations';
export const inject = ['storageDomain', 'sessionProjections', 'sessionController', 'agents', 'subprocess'];
export const Config = Schema.object({
  revision: Schema.string().default('live-production-v1'),
  python: Schema.string().default('python3'),
  environment: Schema.dict(Schema.string()).default({}),
});

const notices = defineDomain({name:'benchforge_production',version:1,tables:{sessions:domainTable(z.object({
  sessionId:z.string(),workspace:z.string(),plan:z.string(),run:z.string(),revision:z.number(),
  key:z.string(),suspended:z.boolean(),
  review:z.object({review_id:z.number(),stage:z.string(),artifact:z.string(),deadline:z.number(),state:z.string()}).nullable(),
}))}});

export async function apply(ctx, config) {
  const projection=await ctx.sessionProjections.register(productionProjection);
  const domain=await ctx.storageDomain.open(notices);
  const logger=ctx.logger(name);
  const reviews=createStageReviewClient(ctx.subprocess,config);
  const production=createProductionLifecycle({projection,table:domain.table('sessions'),agents:ctx.agents,
    readReview:readStageReview,advanceReview:reviews.advance,createMessage:createUserMessage,onError:error=>logger.error(error),
    reconcileCaptures:agent=>ctx.get('benchforgeCapture')?.continuation.reconcile(agent),
    restoreAgent:async id=>{
      const result=await ctx.sessionController.resolveAgent(id);
      if(result.error)throw result.error;
      return result.agent;
    }});
  const execution = createExecutionTuning({ctx});
  ctx.effect(() => async () => {await Promise.all([production.dispose(),reviews.dispose()]);await domain.close();execution.dispose();}, name);
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
