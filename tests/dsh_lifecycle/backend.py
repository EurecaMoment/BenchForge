"""External workload for the real DSH cancellation regression."""
import json
import os
from pathlib import Path
import time

Path(os.environ["BENCHFORGE_SMOKE_READY"]).write_text(json.dumps({"pid": os.getpid()}))
while True:
    time.sleep(60)
