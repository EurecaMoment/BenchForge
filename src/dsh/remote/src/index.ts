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

  constructor(private readonly dispatch: WorkbenchDispatch) {}

  @Remote
  read(query: ReadQuery, signal: AbortSignal): Promise<JsonValue> {
    return this.call('GET', query, undefined, signal);
  }

  @Remote
  write(query: WriteQuery, body: JsonValue, signal: AbortSignal): Promise<JsonValue> {
    return this.call('POST', query, body, signal);
  }

  @Remote
  async products(query: ProductsQuery, signal: AbortSignal): Promise<ConversationProducts> {
    return await this.call('GET', {view: 'products', ...query}, undefined, signal) as unknown as ConversationProducts;
  }

  @Remote
  async stageReview(query: StageReviewQuery, signal: AbortSignal): Promise<StageReviewResult> {
    const result = await this.call('GET', {view: 'stage_review', ...query}, undefined, signal);
    if (!result || typeof result !== 'object' || Array.isArray(result) ||
        typeof result.state !== 'string' || typeof result.can_continue !== 'boolean') {
      throw new Error('Invalid stage review response');
    }
    return result as unknown as StageReviewResult;
  }

  @Remote
  async progress(query: ProgressQuery, signal: AbortSignal): Promise<ProgressResult> {
    const result = await this.call('GET', {view: 'progress', ...query}, undefined, signal);
    if (!result || typeof result !== 'object' || Array.isArray(result) ||
        !Array.isArray(result.operations) || !Array.isArray(result.pipelines) || typeof result.snapshot_at !== 'string') {
      throw new Error('Invalid progress response');
    }
    return result as unknown as ProgressResult;
  }

  @Remote
  async sceneArtifacts(query: SceneArtifactsQuery, signal: AbortSignal): Promise<SceneArtifactsResult> {
    const result = await this.call('GET', {view: 'stage_artifacts', ...query}, undefined, signal);
    if (!result || typeof result !== 'object' || Array.isArray(result) || !Array.isArray(result.items)) {
      throw new Error('Invalid scene artifact response');
    }
    return result as unknown as SceneArtifactsResult;
  }

  private async call(method: 'GET' | 'POST', query: Record<string, string>, body: JsonValue | undefined, signal: AbortSignal) {
    if (this.ctx.invocation?.peer.principal.kind !== 'owner') {
      throw new RemoteError('gateway/bad-request', 'Workbench access requires the host owner', {});
    }
    const url = new URL('http://benchforge.invalid');
    url.search = new URLSearchParams(query).toString();
    return this.dispatch(url, {
      method, signal, headers: new Headers(), text: async () => JSON.stringify(body),
    });
  }
}
