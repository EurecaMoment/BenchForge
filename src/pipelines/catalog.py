"""Discover built-in recipes and the catalogs used to compose them."""
import json
from pathlib import Path

from capabilities.benchmark_design.assessment import assessment_template
from capabilities.dataset_authoring.question_writing import contract
from capabilities.task_catalog import benchmark
from pipelines.benchmark.design.production_steps import execution_contract

ROOT = Path(__file__).parent


# 按文件名稳定读取基准和场景生产目录中的自有流水线配方。
def pipeline_files():
    directories = (ROOT / 'benchmark/recipes', ROOT / 'scene_production/recipes')
    files = sorted((path for directory in directories for path in directory.glob('*.json')),
                   key=lambda path: path.name)
    return [json.loads(path.read_text(encoding='utf-8')) for path in files]


# 按查询分区返回计划模板、协议、流水线或能力模板目录及执行契约。
def catalog(args, directory=None, config=None):
    section = args.get('section', 'pipelines')
    if section == 'plan_template':
        return {**assessment_template(), 'execution_contract': execution_contract()}
    if section == 'protocol':
        return benchmark.catalog(args)
    if section == 'pipelines':
        result = {
            'section': section,
            **benchmark.page(pipeline_files(), args),
            'source': benchmark.document()['meta']['version'],
        }
    elif section in ('capabilities', 'tasks', 'templates'):
        result = benchmark.catalog(args)
    else:
        raise ValueError('section must be pipelines, capabilities, tasks, templates, protocol or plan_template')
    return {
        **result,
        'capability_policy': 'Catalog links select candidates; task operation and measurement variant define measured scope.',
        'question_authoring': contract(),
        'execution_contract': execution_contract(),
    }
