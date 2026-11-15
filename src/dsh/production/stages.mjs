import contract from '../../capabilities/benchmark_design/resources/production-stages.json' with {type: 'json'};

export const stageContract = contract;
export const STAGES = contract.stages;
export const COLLECTION_OPERATIONS = new Set(contract.collection_operations);
export const stageName = (id, language = 'zh') => STAGES.find(row => row.id === id)?.[language].name || id;
export const stageSummary = (language = 'zh') => STAGES.map(row => row.id + ' ' + row[language].name).join(' → ');
export const stageOfStep = step => contract.required_operations[step.uses] ||
  (STAGES.some(row => row.id === step.review_stage) ? step.review_stage : contract.default_operations[step.uses]) || null;
export const stageCopy = language => Object.fromEntries(STAGES.flatMap(({key, [language]: text}) => [
  [key + 'Nav', text.nav], [key, text.heading], [key + 'Desc', text.description], [key + 'Output', text.output],
]));
