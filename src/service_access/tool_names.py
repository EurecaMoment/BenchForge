"""Public tool identifiers shared with the DSH registrar and client guidance."""
import json
from pathlib import Path
import re

NAMES = json.loads((Path(__file__).parents[1] / 'capabilities/benchmark_design/resources/tool-names.json').read_text(encoding='utf-8'))
_PATTERN = re.compile(r'\b(' + '|'.join(map(re.escape, NAMES)) + r')\b')


def qualify(text):
    return _PATTERN.sub(lambda match: NAMES[match[0]], text)
