/** 优先沿用显式幂等键，否则使用原生会话和调用标识生成稳定请求键。 */
export function requestKeyFor(args, exec) {
  return args.request_key ?? `dsh:${exec.agent.session.id}:${exec.callId}`;
}
