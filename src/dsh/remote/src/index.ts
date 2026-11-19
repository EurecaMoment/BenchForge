import {Service, type Context} from '@deepseek-ai/cordis';
import type {} from '@deepseek-ai/dsh-api-gateway';
import {Remote, RemoteError, bindTypertRemote} from '@deepseek-ai/dsh-typert-protocol';
import type {ConversationProducts, JsonValue, ProductsQuery, ReadQuery, WriteQuery} from './types.js';
import type {StageReviewQuery, StageReviewResult, ProgressQuery, ProgressResult,
  SceneArtifactsQuery, SceneArtifactsResult} from './types.js';

type WorkbenchDispatch = (url: URL, input: {
  method: 'GET' | 'POST';
  signal: AbortSignal;
  headers: Headers;
  text: () => Promise<string>;
}) => Promise<JsonValue>;

declare module '@deepseek-ai/cordis' {
  interface Context {
    benchforgeWorkbench: WorkbenchRemote;
  }
}

export class WorkbenchRemote {
  readonly typertRemote = bindTypertRemote(this, 'benchforgeWorkbench');

  // Cordis binds the invocation Context when the Gateway gets the service.
  private readonly [Service.tracker] = {property: 'ctx'};
  declare private readonly ctx: Context;

  /** 保存工作台分发接口，供经过身份校验的原生远程方法复用。 */
  constructor(private readonly dispatch: WorkbenchDispatch) {}

  /** 把原生远程读取请求和取消信号转交工作台分发器。 */
  @Remote
  read(query: ReadQuery, signal: AbortSignal): Promise<JsonValue> {
    return this.call('GET', query, undefined, signal);
  }

  /** 把原生远程写入请求、请求体和取消信号转交工作台分发器。 */
  @Remote
  write(query: WriteQuery, body: JsonValue, signal: AbortSignal): Promise<JsonValue> {
    return this.call('POST', query, body, signal);
  }

  /** 读取当前会话的结构化产物列表。 */
  @Remote
  async products(query: ProductsQuery, signal: AbortSignal): Promise<ConversationProducts> {
    return await this.call('GET', {view: 'products', ...query}, undefined, signal) as unknown as ConversationProducts;
  }

  /** 读取阶段复核结果，并验证状态和继续执行标记符合约定。 */
  @Remote
  async stageReview(query: StageReviewQuery, signal: AbortSignal): Promise<StageReviewResult> {
    const result = await this.call('GET', {view: 'stage_review', ...query}, undefined, signal);
    if (!result || typeof result !== 'object' || Array.isArray(result) ||
        typeof result.state !== 'string' || typeof result.can_continue !== 'boolean') {
      throw new Error('Invalid stage review response');
    }
    return result as unknown as StageReviewResult;
  }

  /** 读取生产进度，并检查操作、流水线和快照时间字段。 */
  @Remote
  async progress(query: ProgressQuery, signal: AbortSignal): Promise<ProgressResult> {
    const result = await this.call('GET', {view: 'progress', ...query}, undefined, signal);
    if (!result || typeof result !== 'object' || Array.isArray(result) ||
        !Array.isArray(result.operations) || !Array.isArray(result.pipelines) || typeof result.snapshot_at !== 'string') {
      throw new Error('Invalid progress response');
    }
    return result as unknown as ProgressResult;
  }

  /** 读取场景阶段产物，并校验返回条目集合。 */
  @Remote
  async sceneArtifacts(query: SceneArtifactsQuery, signal: AbortSignal): Promise<SceneArtifactsResult> {
    const result = await this.call('GET', {view: 'stage_artifacts', ...query}, undefined, signal);
    if (!result || typeof result !== 'object' || Array.isArray(result) || !Array.isArray(result.items)) {
      throw new Error('Invalid scene artifact response');
    }
    return result as unknown as SceneArtifactsResult;
  }

  /** 仅允许宿主所有者访问工作台，将查询参数和请求体转换为统一分发请求。 */
  private async call(method: 'GET' | 'POST', query: Record<string, string>, body: JsonValue | undefined, signal: AbortSignal) {
    if (this.ctx.invocation?.peer.principal.kind !== 'owner') {
      throw new RemoteError('gateway/bad-request', 'Workbench access requires the host owner', {});
    }
    const url = new URL('http://benchforge.invalid');
    url.search = new URLSearchParams(query).toString();
    return this.dispatch(url, {
      method, signal, headers: new Headers(), /** 把原生远程请求体序列化为分发器需要的 JSON 文本。 */ text: async () => JSON.stringify(body),
    });
  }
}
