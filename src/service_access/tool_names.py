"""Public tool identifiers shared with the DSH registrar and client guidance."""
import json
from pathlib import Path
import re

NAMES = json.loads((Path(__file__).parents[1] / 'capabilities/benchmark_design/resources/tool-names.json').read_text(encoding='utf-8'))
_PATTERN = re.compile(r'\b(' + '|'.join(map(re.escape, NAMES)) + r')\b')


# 将文本中的已知旧工具名称替换为规范的带领域名称。
def qualify(text):
    return _PATTERN.sub(lambda match: NAMES[match[0]], text)
