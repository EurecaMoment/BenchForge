import Schema from '@deepseek-ai/schemastery';
import {defineDomain, domainTable} from '@deepseek-ai/dsh-storage-domain';
import {launchEnvironmentOf} from '@deepseek-ai/dsh-launch-environment';
import {z} from 'zod';
import {bootstrapExample} from './bootstrap.mjs';

export const name = 'benchforge-examples';
export const inject = ['sessionPersistence', 'workspaceRegistry', 'storageDomain',
  'sessionProjectionCache', 'sessionController', 'sessionTitle'];
export const Config = Schema.object({enabled: Schema.boolean().default(true), sourceDirectory: Schema.string().default('')});
const imports = defineDomain({name: 'benchforge_examples', version: 1, tables: {imports: domainTable(z.object({
  state: z.enum(['preparing', 'complete']), sessionId: z.string(), workspace: z.string(), importedAt: z.number(),
  summaryVersion: z.literal(1).optional(),
  workbenchVersion: z.literal(1).optional(),
}))}});

/** 在启用示例导入时装配资源与会话引导，并把存储域释放绑定到宿主生命周期。 */
export async function apply(ctx, config) {
  if (!config.enabled || !config.sourceDirectory) return;
  const home = launchEnvironmentOf(ctx).get('DSH_HOME')?.value;
  if (!home) throw new Error('BenchForge example import requires the instance DSH_HOME');
  const domain = await ctx.storageDomain.open(imports);
  ctx.effect(() => () => domain.close(), name);
  const result = await bootstrapExample({sourceDirectory: config.sourceDirectory, home,
    persistence: ctx.sessionPersistence, registry: ctx.workspaceRegistry, table: domain.table('imports'),
    /** 根据导入事件生成官方冷快照，使示例立即具有可检索投影摘要。 */
    publishSummary: (header, events) => ctx.sessionProjectionCache.coldSnapshot(header, 0, events)});
  ctx.logger(name).info('Example %s: %s', result.status, result.sessionId);
}
