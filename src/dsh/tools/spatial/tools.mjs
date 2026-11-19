import {Config, inject, registerPythonTools} from '../../host/python_tools.mjs';
import {definitions, schemas} from './contracts.mjs';

export {Config, inject};
export const name = 'spatial-learning-tools';

/** 按空间任务模块的定义和结构注册受管 Python 工具。 */
export function apply(ctx, config) {
  return registerPythonTools(ctx, config, {definitions, schemas, module: 'server.spatial_cli'});
}
