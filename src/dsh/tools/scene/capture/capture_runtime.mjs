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
export const inject = ['benchforgeAccess','storageDomain','agents','sessionController'];
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

/** 装配持久化采集续接、服务读取和会话恢复钩子，并在宿主卸载时回收后台任务。 */
export async function apply(ctx, config) {
  const environment = launchEnvironmentOf(ctx);
  const loaded = await loadServiceConfig(config.productionConfig, {
    /** 从启动环境快照解析采集服务需要的配置值。 */
    getEnvironmentValue: name => environment.getFrom(name, ['process', 'project-env', 'user-env']),
  });
  const {configPath} = loaded;
  const configuredRoots = config.workspacePaths?.length ? config.workspacePaths : [path.dirname(configPath)];
  /** 合并配置根目录和已注册工作区，过滤空路径并去重。 */
  const roots = (cwd, sessionId) => ctx.benchforgeAccess.rootsFor([sessionId], configuredRoots).map(workspace => workspace.path)
    .filter(root => {
      const relative = path.relative(root, cwd);
      return relative !== '..' && !relative.startsWith('..' + path.sep) && !path.isAbsolute(relative);
    });
  const associations = createCaptureAssociations({ctx, roots});
  const domain = await ctx.storageDomain.open(captures), controller = new AbortController(), timers = new Set();
  const service = createServiceClient(loaded);
  const continuation = createCaptureContinuation({table:domain.table('runs'),artifacts:loaded.config.artifacts,
    /** 在当前可信访问作用域内读取采集服务接口。 */
    read:(route,args,signal)=>service(route,args,AbortSignal.any([signal,AbortSignal.timeout(route==='/wait'?75000:15000)])),
    signal:controller.signal,readReview:createStageReviewReader({...config,service}).latest,
    createMessage:createUserMessage,/** 把后台采集续接异常写入宿主日志。 */ onError:error=>ctx.logger(name).error(error),
    /** 通过官方会话控制器取得或恢复指定原生 Agent。 */
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
  ctx.on('ready',/** 宿主就绪后恢复持久化的后台采集观察。 */ ()=>continuation.restore());
  /** 延迟协调已恢复会话的采集状态，避免在组合阶段直接驱动会话。 */
  function recover(agent) {
    // Composition cannot drive an agent. Recover after its creation boundary.
    const timer = setTimeout(/** 在延迟恢复点绑定原生 Agent 并协调其最新采集，记录异常且卸载后停止工作。 */ async()=>{
      timers.delete(timer);
      if (controller.signal.aborted) return;
      try {
        await continuation.resume(agent);
        const cwd=agent.session.header?.cwd;
        if (cwd && roots(cwd, agent.id).length && agent.session.header?.origin !== 'subagent')
          await continuation.reconcile(agent,{recover:true});
      } catch(error) {ctx.logger(name).error(error);}
    },0);
    timer.unref?.();timers.add(timer);
  }
  ctx.on('agent/created',/** 在 Agent 完成创建后安排延迟恢复，避免组合阶段直接驱动会话。 */ ({agent})=>recover(agent));
  for (const agent of ctx.agents.list()) recover(agent);
  ctx.on('agent/turn-stopping',/** 结束轮次前登记仍需宿主持有的采集等待，保留未完成任务。 */ payload=>continuation.stopping(payload));
  ctx.on('agent/pre-step', /** 官方准入允许后同步父生产会话的采集关联与续接状态，再进入下一步。 */ async ({agent,signal}, next) => {
    const decision = await next();
    if (decision.kind !== 'enter') return decision;
    const cwd = agent.session.header?.cwd;
    if (cwd && roots(cwd, agent.id).length && agent.session.header?.origin !== 'subagent') {
      await associations.record(agent, agent.session.snapshotEvents().slice(-64));
      await continuation.reconcile(agent,{signal});
    }
    return decision;
  });
}
