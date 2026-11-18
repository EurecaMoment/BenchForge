import path from 'node:path';
import {launchEnvironmentOf} from '@deepseek-ai/dsh-launch-environment';
import Schema from '@deepseek-ai/schemastery';
import {createServiceClient,loadServiceConfig} from '../service_client.mjs';
import {createCaptureAssociations} from './capture_associations.mjs';
import {createCaptureContinuation} from './continuation.mjs';
import {defineDomain, domainTable} from '@deepseek-ai/dsh-storage-domain';
import {createUserMessage} from '@deepseek-ai/dsh-llm';
import {z} from 'zod';
import {createStageReviewReader,reviewReadConfig} from '../../../production/reviews.mjs';

export const name = 'benchforge-capture';
export const inject = ['workspaceRegistry','storageDomain','agents','sessionController'];
export const Config = Schema.object({
  ...reviewReadConfig,
  productionConfig: Schema.string().default(''),
  workspacePaths: Schema.array(Schema.string()).default([]),
  revision: Schema.string().default('live-production-v1'),
});
const captures = defineDomain({name:'benchforge_capture_continuation',version:1,tables:{runs:domainTable(z.object({
  owner:z.string(),run_id:z.string(),generation:z.string(),submitted_at:z.number(),state:z.string(),
  observation:z.record(z.string(),z.unknown()).optional(),error:z.string().optional(),notice_key:z.string().optional(),
  finished_at:z.number().optional(),blocked_at:z.number().optional(),blocker:z.record(z.string(),z.unknown()).optional(),
  scene_id:z.string().optional(),task_seq:z.number().optional(),
  continuation:z.object({progress:z.string(),attempts:z.number().int().nonnegative(),notice:z.string()}).optional(),
}))}});

export async function apply(ctx, config) {
  const environment = launchEnvironmentOf(ctx);
  const loaded = await loadServiceConfig(config.productionConfig, {
    getEnvironmentValue: name => environment.getFrom(name, ['process', 'project-env', 'user-env']),
  });
  const {configPath} = loaded;
  const configuredRoots = config.workspacePaths?.length ? config.workspacePaths : [path.dirname(configPath)];
  const roots = cwd => [...configuredRoots, ...ctx.workspaceRegistry.list().map(workspace => workspace.path)]
    .filter(root => {
      const relative = path.relative(root, cwd);
      return relative !== '..' && !relative.startsWith('..' + path.sep) && !path.isAbsolute(relative);
    });
  const associations = createCaptureAssociations({ctx, roots});
  const domain = await ctx.storageDomain.open(captures), controller = new AbortController(), timers = new Set();
  const service = createServiceClient(loaded);
  const continuation = createCaptureContinuation({table:domain.table('runs'),artifacts:loaded.config.artifacts,
    read:(route,args,signal)=>service(route,args,AbortSignal.any([signal,AbortSignal.timeout(route==='/wait'?75000:15000)])),
    signal:controller.signal,readReview:createStageReviewReader(config).latest,
    createMessage:createUserMessage,onError:error=>ctx.logger(name).error(error),
    resolveAgent:async id=>{
      const result = await ctx.sessionController.resolveAgent(id);
      if (result.error) throw result.error;
      return result.agent;
    }});
  ctx.provide('benchforgeCapture', {associations,continuation});
  ctx.effect(()=>async()=>{
    controller.abort();for (const timer of timers) clearTimeout(timer);
    await continuation.dispose();await domain.close();
  },'benchforge capture continuation');
  ctx.on('ready',()=>continuation.restore());
  function recover(agent) {
    // Composition cannot drive an agent. Recover after its creation boundary.
    const timer = setTimeout(async()=>{
      timers.delete(timer);
      if (controller.signal.aborted) return;
      try {
        await continuation.resume(agent);
        const cwd=agent.session.header?.cwd;
        if (cwd && roots(cwd).length && agent.session.header?.origin !== 'subagent')
          await continuation.reconcile(agent,{recover:true});
      } catch(error) {ctx.logger(name).error(error);}
    },0);
    timer.unref?.();timers.add(timer);
  }
  ctx.on('agent/created',({agent})=>recover(agent));
  for (const agent of ctx.agents.list()) recover(agent);
  ctx.on('agent/turn-stopping',payload=>continuation.stopping(payload));
  ctx.on('agent/pre-step', async ({agent,signal}, next) => {
    const decision = await next();
    if (decision.kind !== 'enter') return decision;
    const cwd = agent.session.header?.cwd;
    if (cwd && roots(cwd).length && agent.session.header?.origin !== 'subagent') {
      await associations.record(agent, agent.session.snapshotEvents().slice(-64));
      await continuation.reconcile(agent,{signal});
    }
    return decision;
  });
}
