/** 创建按会话隔离的工作台视图存储，协调持久化路由和生产需求。 */
export function createWorkbenchViewStore(defineStore) {
  return defineStore({
    /** 为会话构造初始路由、需求和初始化状态。 */
    init: () => ({route: null, demand: null}),
    persist: 'bfw:workbench-view:v1',
    actions: {
      /** 载入并保存指定会话的初始工作台状态。 */
      initialize: (state, route, demand) => {
        state.route ??= route;
        state.demand ??= demand;
      },
      /** 合并路由变化并持久化当前会话的浏览位置。 */
      updateRoute: (state, patch) => {Object.assign(state.route, patch);},
      /** 合并生产需求字段并写入当前会话的本地保存记录。 */
      changeDemand: (state, patch) => {Object.assign(state.demand, patch);},
      /** 切换需求编辑模式并保留现有生产上下文。 */
      requestChange: (state) => {state.demand.mode = 'append';},
      /** 导航到场景选择界面并保留当前会话其他视图状态。 */
      openScenes: (state) => {state.route.stage = 'S2';},
    },
  });
}
