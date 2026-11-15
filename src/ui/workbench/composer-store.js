export function createWorkbenchViewStore(defineStore) {
  return defineStore({
    init: () => ({route: null, demand: null}),
    persist: 'bfw:workbench-view:v1',
    actions: {
      initialize: (state, route, demand) => {
        state.route ??= route;
        state.demand ??= demand;
      },
      updateRoute: (state, patch) => {Object.assign(state.route, patch);},
      changeDemand: (state, patch) => {Object.assign(state.demand, patch);},
      requestChange: (state) => {state.demand.mode = 'append';},
      openScenes: (state) => {state.route.stage = 'S2';},
    },
  });
}
