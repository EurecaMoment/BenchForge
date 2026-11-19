import {canStartProduction} from './production-permissions.js';

export const PRODUCTION_SOURCE = 'benchforge-production';

/** 注册与原生输入框集成的生产配置来源，负责配置块更新、验证和发送时序列化。 */
export function createProductionInput({ctx, requestWorkbench, productionRequest, productionContext, t}) {
  /** 取得指定会话绑定的原生输入控制器。 */
  const inputFor = (sessionId) => ctx.conversation.input.for(ctx.sessions.binding(sessionId).ctx);
  /** 只在输入框空闲且配置块位置正确时插入、替换或移除当前生产设置。 */
  const configureProduction = (sessionId, demand, enabled) => {
    const input = inputFor(sessionId);
    const state = input.state.getSnapshot();
    if (state.phase !== 'plain' || state.claim) throw new Error(t('draftBusy'));
    const occurrence = state.occurrences.find((row) => row.source === PRODUCTION_SOURCE);
    if (occurrence && occurrence.offset !== 0) throw new Error(t('productionMoved'));
    const span = {start: 0, end: occurrence ? 1 : 0, draftRev: state.draftRev};
    if (!enabled) {
      if (occurrence && !ctx.sessions.binding(sessionId).ctx.bail('slash/input-consume-token', {guard: {kind: 'span', span}})) {
        throw new Error(t('draftBusy'));
      }
      return;
    }
    if (!occurrence && state.draft.trimStart().startsWith('/')) throw new Error(t('productionCommandHint'));
    const applied = input.insertReference({source: PRODUCTION_SOURCE,
      ref: JSON.stringify({version: 1, sessionId, demand}),
      label: t('productionSettings'), clipboardText: '[' + t('productionSettings') + ']'}, span);
    if (!applied) throw new Error(t('draftBusy'));
  };
  const source = {
    trigger: '@', name: PRODUCTION_SOURCE, showGroupTitle: false,
    /** 生产配置仅由显式界面插入，因此不提供自动补全候选。 */
    candidates: async () => [],
    /** 保留输入来源的选择接口；生产配置由专门设置界面处理。 */
    onPick: () => undefined,
    codec: {
      /** 提供复制配置块时使用的简短本地化占位文本。 */
      clipboardText: () => '[' + t('productionSettings') + ']',
      /** 验证配置块版本和字段，读取当前场景约束后生成真正发送的生产指令与上下文。 */
      serialize: async (ref, signal) => {
        let value;
        try {value = JSON.parse(ref);} catch {throw new Error(t('productionConfigInvalid'));}
        if (value?.version !== 1 || typeof value.sessionId !== 'string' || !value.demand ||
            !['new', 'append'].includes(value.demand.mode) ||
            typeof value.demand.capability !== 'string' ||
            !['production', 'exploration'].includes(value.demand.executionMode) ||
            !['exact', 'minimum', 'best_effort'].includes(value.demand.quantityContract)) {
          throw new Error(t('productionConfigInvalid'));
        }
        const {sessionId, demand} = value;
        const binding = ctx.sessions.binding(sessionId);
        if (binding.session.projections.faceOf('agentPreset').getSnapshot() !== 'benchforge') throw new Error(t('productionConfigInvalid'));
        if (!canStartProduction(binding.session.projections.faceOf('permissions').getSnapshot())) {
          throw new Error(t('permissionsPending'));
        }
        const input = inputFor(sessionId).state.getSnapshot();
        const occurrence = input.occurrences.find((row) => row.source === PRODUCTION_SOURCE && row.ref === ref);
        if (!occurrence) throw new Error(t('productionConfigInvalid'));
        const objective = input.draft.slice(0, occurrence.offset) + input.draft.slice(occurrence.offset + occurrence.length);
        if (!objective.trim() && !input.attachmentIds.length) throw new Error(t('requestRequired'));
        if (demand.mode !== 'append') {
          if (!Number.isSafeInteger(Number(demand.count)) || Number(demand.count) <= 0) throw new Error(t('positiveCount'));
          if (demand.sceneCount && (!Number.isSafeInteger(Number(demand.sceneCount)) || Number(demand.sceneCount) <= 0)) throw new Error(t('positiveSceneCount'));
          if (demand.storageBudget && (!Number.isFinite(Number(demand.storageBudget)) || Number(demand.storageBudget) <= 0)) throw new Error(t('positiveBudget'));
        }
        const workspacePath = ctx.sessions.list.getSnapshot().byId[sessionId]?.cwd;
        const query = {workspace_path: workspacePath, session_id: sessionId};
        const [selection, template, review] = await Promise.all([
          requestWorkbench({...query, view: 'scene_selection'}, {signal}),
          requestWorkbench({...query, view: 'plan_template'}, {signal}),
          requestWorkbench({...query, view: 'stage_review'}, {signal}),
        ]);
        signal.throwIfAborted();
        if (selection.error || review.error) throw new Error(selection.error || review.error);
        if (!selection.sources?.length && !selection.allow_new_scenes) throw new Error(t('chooseSceneRequired'));
        if (template.schema !== 'benchforge.assessment-template/v1' || !template.assessment) throw new Error(t('templateReadFailed'));
        let prefix = productionRequest({...demand, objective: t('composerObjective')}) + productionContext(selection, template);
        if (['waiting_user', 'paused', 'needs_input', 'check_failed'].includes(review.state)) {
          const result = await requestWorkbench({...query, view: 'stage_review'}, {
            body: {action: 'instruction', review_id: review.review_id, instruction: objective}, signal,
          });
          if (result.error) throw new Error(result.error);
          if (binding.session.getSnapshot().running) {
            const result = await binding.session.cancel();
            if (!result.ok) throw new Error(result.error.message);
          }
          prefix = '阶段等待中的用户指令已保存。先读取 stage_review(action=status)，应用以下要求后再处理后续阶段；不要绕过等待关口。\n' + prefix;
        }
        signal.throwIfAborted();
        return prefix + '\n\n';
      },
    },
  };
  return {configureProduction, source};
}
