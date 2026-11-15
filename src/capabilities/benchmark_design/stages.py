"""Stage semantics shared by the planner, runner, preset and workbench."""
import json
from pathlib import Path

CONTRACT = json.loads((Path(__file__).parent / 'resources/production-stages.json').read_text(encoding='utf-8'))
STAGES = tuple(row['id'] for row in CONTRACT['stages'])
COLLECTION_OPERATIONS = frozenset(CONTRACT['collection_operations'])


def stage_of(step):
    operation = step.get('uses', '')
    required = CONTRACT['required_operations'].get(operation)
    if required:
        return required
    if step.get('review_stage') in STAGES:
        return step['review_stage']
    return CONTRACT['default_operations'].get(operation)


def stage_summary(language='zh'):
    return ' → '.join(row['id'] + ' ' + row[language]['name'] for row in CONTRACT['stages'])
