import {createHash} from 'node:crypto';
import path from 'node:path';
import Schema from '@deepseek-ai/schemastery';
import {SessionQueryEngine, SessionQueryError, buildSessionEventSearchDocuments,
  materializeSessionEventResultFilters} from '@deepseek-ai/dsh-session-query';
import {launchEnvironmentOf} from '@deepseek-ai/dsh-launch-environment';
import {loadServiceConfig, createServiceClient} from '../tools/scene/service_client.mjs';
import {waitWithSignal} from '../runtime/cancellation.mjs';

/** 对可序列化身份或内容计算 SHA-256 摘要，生成稳定的宿主或索引标识。 */
const digest = value => createHash('sha256').update(JSON.stringify(value)).digest('hex');
/** 构造带会话查询错误码的服务异常。 */
const failure = (message, code) => new SessionQueryError(message, 'SESSION_QUERY_' + code);

/** Official exact-read/restore behavior with a PostgreSQL search read model. */
export default class PostgresSessionQuery extends SessionQueryEngine {
  static inject = ['sessions', 'sessionPersistence'];
  static Config = Schema.object({productionConfig: Schema.string().required()});

  /** 建立数据库会话查询服务，持有宿主身份、索引版本和统一取消寿命。 */
  constructor(ctx, config) {
    super(ctx);
    this.owner = ctx;
    this.revisions = new Map();
    this.queue = Promise.resolve();
    this.lifetime = new AbortController();
    ctx.effect(() => () => this.lifetime.abort(), 'postgres-session-query');
    const environment = launchEnvironmentOf(ctx);
    this.hostId = digest(environment.get('DSH_HOME')?.value || path.resolve(config.productionConfig));
    this.client = /** 按需建立后端服务客户端；连接失败后清除缓存以允许后续重试。 */ () => this.service ||= loadServiceConfig(config.productionConfig, {
      /** 从宿主启动时保存的环境快照解析服务配置引用。 */
      getEnvironmentValue: name => environment.get(name)?.value,
    }).then(createServiceClient).catch(error => { this.service = null; throw error; });
  }

  /** 仅为版本变化的会话重建事件搜索文档，并记录实际索引的不可变观察版本。 */
  async synchronize(records, signal) {
    const service = await this.client(), versions = [];
    for (const record of records) {
      signal.throwIfAborted();
      const id = record.header.id, live = this.owner.sessions.get(id);
      const revision = live ? 'live:' + live.seq : JSON.stringify((await this.owner.sessionPersistence.stat(id))?.revision);
      if (this.revisions.get(id) !== revision) {
        const observation = await this.observeSession(id, {signal, projectionMode: 'none'});
        try {
          const documents = buildSessionEventSearchDocuments(id, observation.events);
          await service('/session-search/replace', {host_id: this.hostId, session_id: id, documents}, signal);
          // Use the immutable cut actually indexed, never a later live revision.
          this.revisions.set(id, observation.source === 'live' ? 'live:' + (observation.cursor + 1) : JSON.stringify(observation.revision));
          record.header = structuredClone(observation.header);
        } finally { observation[Symbol.dispose](); }
      }
      versions.push([id, this.revisions.get(id)]);
    }
    return digest(versions.sort(([a], [b]) => a.localeCompare(b)));
  }

  /** 将索引协调和查询放入同一串行代际，限制查询时长并保留原生取消语义。 */
  async search(request, exec = {}, grouped) {
    const signal = AbortSignal.any([this.lifetime.signal, AbortSignal.timeout(30000), ...(exec.signal ? [exec.signal] : [])]);
    // Keep reconcile + query as one generation. Callers can cancel even while queued.
    const task = this.queue.catch(() => {}).then(() => this.searchPage(request, signal, grouped));
    this.queue = task.catch(() => {});
    try { return await waitWithSignal(task, signal); }
    catch (error) {
      if (error instanceof SessionQueryError) throw error;
      throw new SessionQueryError(signal.aborted ? 'Session search cancelled or timed out' : 'PostgreSQL session search failed',
        signal.aborted ? 'SESSION_QUERY_ABORTED' : 'SESSION_QUERY_INDEX_FAILED', {cause: error});
    }
  }

  /** 校验查询及筛选范围，同步授权会话的索引后读取一页搜索结果。 */
  async searchPage(request, signal, grouped) {
    signal.throwIfAborted();
    const query = typeof request.query === 'string' ? request.query.trim() : '', limit = request.limit ?? 30;
    if (typeof query !== 'string' || !query || query.length > 500) throw failure('Query must contain 1..500 characters', 'INVALID_QUERY');
    if (!Number.isInteger(limit) || limit < 1 || limit > 100) throw failure('Limit must be between 1 and 100', 'INVALID_LIMIT');
    const filters = materializeSessionEventResultFilters(request.eventFilters || request.filters || []);
    if (filters.some(row => row.kind === 'text')) throw failure('Use query for text search', 'INVALID_FILTER');
    const records = await this.filterSessions(grouped ? request.sessionFilters || [] : [{kind: 'id', values: [request.sessionId]}], signal);
    if (!grouped && !records.length) throw failure('Session not found', 'SESSION_NOT_FOUND');
    const key = digest({...request, query, limit, cursor: undefined, grouped});
    let cursor;
    if (request.cursor) {
      try {
        cursor = JSON.parse(Buffer.from(request.cursor, 'base64url').toString());
        if (cursor.key !== key || !Number.isSafeInteger(cursor.offset) || cursor.offset < 0) throw Error();
      } catch { throw failure('Invalid search cursor', 'INVALID_CURSOR'); }
    }
    const generation = await this.synchronize(records, signal);
    if (cursor && cursor.generation !== generation) throw failure('Search corpus changed; restart this search', 'STALE_CURSOR');
    const offset = cursor?.offset || 0, service = await this.client();
    const result = await service('/session-search/query', {host_id: this.hostId,
      sessions: records.map(row => row.header.id), query, filters, grouped, offset, limit}, signal);
    const byId = new Map(records.map(row => [row.header.id, row]));
    return {
      items: grouped ? result.items.map(hit => ({...byId.get(hit.sessionId), bestMatch: hit})) : result.items,
      ...(!grouped ? {session: records[0].header} : {}),
      ...(result.hasMore ? {nextCursor: Buffer.from(JSON.stringify({key, generation, offset: offset + result.items.length})).toString('base64url')} : {}),
    };
  }

  /** 执行按会话分组的搜索，复用统一索引与权限校验流程。 */
  searchSessions(request, exec) { return this.search(request, exec, true); }
  /** 搜索具体会话事件，返回未按会话聚合的匹配项。 */
  searchEvents(request, exec) { return this.search(request, exec, false); }
}
