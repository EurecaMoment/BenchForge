"""Small per-revision receipts; no traversal of large meshes or model streams."""
import json
from pathlib import Path
import time


# 定位当前修订目录并原子更新带纳秒时间的阶段进度；非修订目录不写入。
def record_progress(directory,stage,**details):
    folder=Path(directory)
    if not folder.name.startswith('revision_'):
        folder=next((p for p in folder.parents if p.name.startswith('revision_')),None)
    if folder is None:return
    record={'stage':stage,'updated_ns':time.time_ns(),**details}
    temporary=folder/'progress.json.tmp';temporary.write_text(json.dumps(record,ensure_ascii=False),encoding='utf8')
    temporary.replace(folder/'progress.json')
