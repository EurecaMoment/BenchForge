import contract from '../../capabilities/benchmark_design/resources/production-stages.json' with {type: 'json'};

export const stageContract = contract;
export const STAGES = contract.stages;
export const COLLECTION_OPERATIONS = new Set(contract.collection_operations);
/** 按语言返回生产阶段名称，未知阶段保留原始标识。 */
export const stageName = (id, language = 'zh') => STAGES.find(row => row.id === id)?.[language].name || id;
/** 按标准顺序生成指定语言的完整阶段流程说明。 */
export const stageSummary = (language = 'zh') => STAGES.map(row => row.id + ' ' + row[language].name).join(' → ');
/** 优先依据必需操作和显式复核阶段，推断计划步骤所属阶段。 */
export const stageOfStep = step => contract.required_operations[step.uses] ||
  (STAGES.some(row => row.id === step.review_stage) ? step.review_stage : contract.default_operations[step.uses]) || null;
/** 从统一阶段定义生成界面导航、标题、说明和输出文案。 */
export const stageCopy = language => Object.fromEntries(STAGES.flatMap(({key, [language]: text}) => [
  [key + 'Nav', text.nav], [key, text.heading], [key + 'Desc', text.description], [key + 'Output', text.output],
]));
