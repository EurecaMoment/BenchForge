import {dictionaries} from './i18n.js';
import {styles} from './styles.js';
import {createPrimitives} from './primitives.js';
import {createWorkbenchState} from './state.js';
import {createApiHooks, createWorkbenchRequest} from './api.js';
import {createProgressComponents} from './components/progress.js';
import {createGalleryComponents} from './components/gallery.js';
import {createProductComponents} from './components/products.js';
import {createWorkbenchComponent} from './components/workbench.js';
import {createComposerComponents} from './components/composer.js';
import {createWorkbenchViewStore} from './composer-store.js';
import {createProductionInput} from './production-input.js';
import {createProductsResource, productsResourceAddress} from './products-resource.js';
import workbenchRemote from '../../dsh/remote/lib/typert.remote-client.js';

const PRESET_ID = 'benchforge';
const WORKBENCH_TAB = '@eurecamoment/benchforge-ui/workbench';
const WORKBENCH_KIND = 'benchforge-workbench';
const NS = 'benchforgeWorkbench';
const STAGES = [
  {id: 'S1', key: 'blueprint'}, {id: 'S2', key: 'collection'}, {id: 'S3', key: 'structure'},
  {id: 'S4', key: 'synthesis'}, {id: 'S5', key: 'verification'},
];
const PLAN_SECTIONS = [
  ['objectives', '评测目标与边界'], ['coverage', '能力、模板与题量分配'], ['scenes', '场景与采集设计'],
  ['annotations', '标注与真值来源'], ['questions', '出题规则与公开样例'], ['dataset', '数据集规模与分区'],
  ['scoring', '评分方法与对照'], ['quality_gates', '质量检查与验收'], ['execution', '五阶段执行安排'],
  ['delivery', '交付物与验收入口'], ['risks', '风险与待解决事项'],
];
const PLAN_FIELDS = {
  id: '编号', capability: '测量能力', research_question: '具体评测问题', success_criterion: '成功条件', exclusions: '范围边界',
  objective_id: '目标编号', template_id: '模板', count: '题数', difficulty: '难度', conditions: '测量条件', selection_reason: '选择理由',
  source_policy: '来源策略', selection_path: '场景选择记录', selected_scenes: '选用场景', allow_new_scenes: '允许新增场景',
  target_scene_count: '目标场景数', designs: '场景设计', name: '场景名称', purpose: '测量用途', layout: '布局', objects: '物体配置',
  cameras: '视角与采集', variation: '变化维度', artifact: '预览产物', reuse_constraints: '复用约束', preview_policy: '视频或全景展示',
  authority_source: '真值来源', fields: '字段与单位', derivation: '推导方法', alignment: '媒体与标注对齐', validation: '校验规则',
  public_observations: '公开观测', answerability: '可回答性', output_contract: '输出格式', difficulty_design: '难度设计',
  anti_shortcut_checks: '捷径与歧义检查', example: '公开题目样例', target_items: '目标题量', split_policy: '分区策略', seed: '随机种子',
  grouping: '分组单位', deduplication: '去重规则', balance: '分布平衡', metrics: '逐模板评分', metric: '指标', unit: '单位',
  tolerance: '容差', aggregation: '聚合方式', missing_response: '缺失与无效响应', baselines: '对照基线', model_evaluation: '模型评测安排',
  check: '检查内容', threshold: '通过阈值', evidence: '证据位置', on_failure: '失败处理', stage: '阶段', action: '执行动作',
  inputs: '输入', outputs: '输出', completion_condition: '完成条件', artifacts: '交付文件', path: '保存位置', format: '格式',
  contents: '内容', acceptance: '交付验收', issue: '风险或限制', impact: '影响', mitigation: '应对方式', status: '状态',
  task_id: '场景任务', revision: '版本', run_id: '运行', program_path: '场景程序', source_directory: '来源目录',
  geometry_exceptions: '简化几何实验声明', reason: '简化原因', simplifications: '简化内容与范围',
  retained_constraints: '保留的物理与实验约束', excluded_claims: '能力边界', summary: '场景摘要',
  hard_requirements: '必须满足的要求', object_roles: '物体用途', role: '用途与位置', relations: '空间与使用关系',
  description: '具体说明', physical_assumptions: '物理假设与验证范围', revision_history: '设计修订依据', changes: '修改内容', type: '关系',
};

function createClient(React, defineStore, ui) {
  const h = React.createElement;
  const primitives = createPrimitives({React, PLAN_FIELDS, PLAN_SECTIONS});
  const state = createWorkbenchState({STAGES, sceneName: primitives.sceneName});
  const hooks = createApiHooks({React});
  const helpers = {...primitives, ...state, ...hooks, productsResourceAddress, STAGES, PLAN_SECTIONS, PLAN_FIELDS, styles, dictionaries};
  const progress = createProgressComponents({React, helpers});
  const gallery = createGalleryComponents({React, helpers});
  const components = {...progress, ...gallery, ...createProductComponents({React, helpers, components: {...progress, ...gallery}})};
  const composer = createComposerComponents({React, helpers, ui});
  const {Workbench} = createWorkbenchComponent({React, helpers, components});

  function ProductionEnvironment({t, enableProductionPermission, component: Component = Workbench, ...props}) {
    const permissions = props.useProjection('permissions');
    const openState = props.useSession((session) => session.openState);
    const ready = permissions?.currentValue === 'danger-full-access';
    const [permissionError, setPermissionError] = React.useState('');
    const [requestingPermission, setRequestingPermission] = React.useState(false);
    const requestPermission = async () => {
      setRequestingPermission(true);
      setPermissionError('');
      try {
        if (openState !== 'open') throw new Error(t('permissionUnavailable'));
        await enableProductionPermission();
      } catch (error) {
        setPermissionError(error.message);
      } finally {
        setRequestingPermission(false);
      }
    };
    return h(Component, {...props, t, canSubmit: ready, permissionError, requestPermission, requestingPermission});
  }

  async function apply(ctx) {
    await ctx.remote.$mount(workbenchRemote);
    ctx.effect(() => ctx.locale.register(NS, dictionaries));
    const t = ctx.locale.bind(NS);
    ctx.inject(['remote.benchforgeWorkbench', 'resources'], (workbenchCtx) => {
      const requestWorkbench = createWorkbenchRequest(workbenchCtx.remote);
      workbenchCtx.effect(() => workbenchCtx.resources.register(createProductsResource(workbenchCtx.remote)),
        'benchforge-workbench: conversation products resource');
      const sidebar = workbenchCtx.sidebarRight;
      const store = createWorkbenchViewStore(defineStore);
      const production = createProductionInput({ctx: workbenchCtx, requestWorkbench,
        productionRequest: helpers.productionRequest, productionContext: helpers.productionContext, t});
      workbenchCtx.effect(() => workbenchCtx.inputTriggers.registerSource(production.source));
      const environment = (sessionId, actions) => {
        actions.initialize(helpers.initialState(sessionId), helpers.initialDemand(sessionId));
        return {
          requestWorkbench,
          configureProduction: (demand, enabled) => production.configureProduction(sessionId, demand, enabled),
          enableProductionPermission: async () => {
            const result = await workbenchCtx.sessions.binding(sessionId).session.command('/permission danger-full-access');
            if (!result.ok || !result.value.matched) throw new Error(result.error?.message || t('permissionFailed'));
          },
          stopExecution: async () => {
            const result = await workbenchCtx.sessions.binding(sessionId).session.cancel();
            if (!result.ok) throw new Error(result.error?.message || t('stopFailed'));
          },
          openScenes: () => {actions.openScenes(); sidebar.openTab(WORKBENCH_KIND);},
        };
      };
      workbenchCtx.effect(() => workbenchCtx.sidebarRightTabs.register({
        id: WORKBENCH_TAB, kind: WORKBENCH_KIND, title: () => t('panel'), keepMounted: true,
      }));
      workbenchCtx.slots.inject('sidebar.right.pane.tab', () => workbenchCtx.slots.register({
        name: 'sidebar.right.pane.tab', key: WORKBENCH_TAB, locale: NS, store, inject: environment,
      }, function WorkbenchPanel(props) {
        const preset = props.useProjection('agentPreset');
        if (preset !== PRESET_ID) return null;
        return h(ProductionEnvironment, {...props, key: props.sessionId, t});
      }));
      for (const slot of ['conversation.composer.dock', 'conversation.input.dock']) {
        workbenchCtx.slots.inject(slot, () => workbenchCtx.slots.register({
          name: slot, id: 'benchforge-production-settings', order: 10, locale: NS,
          store, inject: environment,
        }, function WorkbenchComposer(props) {
          const preset = props.useProjection('agentPreset');
          const blank = props.useSessions((sessions) => sessions.byId[props.sessionId]?.blank);
          const hero = slot === 'conversation.input.dock';
          if (preset !== PRESET_ID || (hero && !blank)) return null;
          return h(ProductionEnvironment, {...props, key: props.sessionId, t, hero, component: composer.ProductionComposer});
        }));
      }
      const toggle = () => {
        const active = sidebar.active();
        if (active?.kind === WORKBENCH_KIND && sidebar.isExpanded()) sidebar.hide(active.id);
        else sidebar.openTab(WORKBENCH_KIND);
      };
      for (const slot of ['conversation.session.header.utilities', 'conversation.hero.options']) {
        workbenchCtx.slots.inject(slot, () => workbenchCtx.slots.register({
          name: slot, id: 'benchforge-workbench-toggle', order: 10, locale: NS,
        }, function WorkbenchToggle({useProjection}) {
          const preset = useProjection('agentPreset');
          if (preset !== PRESET_ID) return null;
          return h('button', {
            type: 'button', onClick: toggle, title: t('toggleWorkbench'),
            'aria-label': t('toggleWorkbench'), 'data-workbench-toggle': true,
            style: {padding: '.35rem .65rem', borderRadius: '.5rem', cursor: 'pointer',
              border: '1px solid var(--dsw-alias-border-l3)', whiteSpace: 'nowrap',
              background: 'var(--dsw-alias-bg-base)', color: 'var(--dsw-alias-label-primary)',
              font: 'inherit'},
          }, t('panel'));
        }));
      }
    });
  }

  const {productionContext, ...publicHelpers} = helpers;
  return {ProductionEnvironment, Workbench, dictionaries, apply, createWorkbenchRequest, ...publicHelpers, ...components};
}

export default function createPlugin(require) {
  const React = require('react');
  const {defineStore} = require('@deepseek-ai/dsh-client-store');
  const ui = require('@deepseek-ai/dsh-client-ui-primitives');
  const client = createClient(React, defineStore, ui);
  const {Workbench, dictionaries, sceneCollectionProgress, stageState, StageProgress, executionStage, sceneStage, sceneProjects} = client;
  return {
    inject: ['slots', 'locale', 'remote', 'sessions', 'sidebarRight', 'sidebarRightTabs', 'conversation', 'inputTriggers'],
    apply: client.apply,
    Workbench, dictionaries, sceneCollectionProgress, stageState, StageProgress, executionStage, sceneStage, sceneProjects,
    ...client,
  };
}
