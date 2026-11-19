"""Execute task Python in its Host-owned conversation workspace."""

import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import time
import uuid

from service_access.task_paths import task_directory
from service_access.access_scope import current_access
from service_access.processes import terminate_process_tree
from server.task_runtime import python_command
from storage.confined_files import copy_output, open_directory, open_write


# 通过固定目录描述符列举本次执行后的草稿元数据，不打开文件内容并限制扫描规模。
def _artifacts(workspace_fd, workspace, started, call_id):
    """List draft metadata through the pinned directory without opening contents."""
    artifacts = []
    inspected = 0
    for directory, folders, files, descriptor in os.fwalk('.', dir_fd=workspace_fd, follow_symlinks=False):
        inspected += len(folders) + len(files)
        if inspected > 10000:
            break
        for name in files:
            if name.startswith(call_id):
                continue
            try:
                value = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            except FileNotFoundError:
                continue
            if stat.S_ISREG(value.st_mode) and value.st_mtime >= started:
                relative = Path(directory) / name
                artifacts.append({'file': relative.as_posix(), 'source_path': str(workspace / relative),
                                  'bytes': value.st_size})
                if len(artifacts) >= 50:
                    return artifacts
    return artifacts


# 刷新并回卷可信输出流，再安全发布到固定工作区目录。
def _publish(workspace_fd, name, stream):
    stream.flush()
    stream.seek(0)
    copy_output(workspace_fd, stream, name)


# 校验代码及超时，在受控会话任务目录运行隔离 Python 并收集输出和草稿元数据。
def run_task_code(artifact_root, workspace_id, code, timeout_seconds=60):
    if not isinstance(code, str) or not 1 <= len(code) <= 1024 * 1024:
        raise ValueError("code must be 1..1048576 characters")
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 300:
        raise ValueError("timeout_seconds must be 1..300")
    if not shutil.which("bwrap"):
        raise RuntimeError("Task-code sandbox requires bubblewrap (bwrap) on the backend service PATH")
    workspace = task_directory(workspace_id)
    conversation_root = Path(current_access().root_path).resolve()
    call_id = "code_" + uuid.uuid4().hex[:12]
    script = workspace / (call_id + ".py")
    # Keep code and process output private until execution ends. Task-controlled
    # names cannot replace interpreter input or cause us to read host secrets.
    with (open_directory(conversation_root) as conversation_fd,
          open_directory(conversation_fd, workspace.relative_to(conversation_root)) as workspace_fd,
          tempfile.TemporaryFile() as source, tempfile.TemporaryFile() as out,
          tempfile.TemporaryFile() as err):
        source.write(code.encode('utf8'))
        _publish(workspace_fd, script.name, source)
        source.seek(0)
        command = python_command(workspace, script, workspace_fd=workspace_fd, script_fd=source.fileno(),
                                 conversation_root=conversation_root, conversation_fd=conversation_fd)
        started, timed_out = time.time(), False
        process = subprocess.Popen(command, stdout=out, stderr=err,
            stdin=subprocess.DEVNULL, env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
            start_new_session=True, pass_fds=(workspace_fd, source.fileno(), conversation_fd))
        try:
            process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            terminate_process_tree(process)
            process.wait(timeout=10)
        _publish(workspace_fd, call_id + '.stdout', out)
        _publish(workspace_fd, call_id + '.stderr', err)
        out.seek(0)
        err.seek(0)
        result = {"workspace_id": workspace_id, "workspace_path": str(workspace), "call_id": call_id,
              "conversation_path": str(conversation_root),
              "returncode": process.returncode, "timed_out": timed_out,
              "stdout": out.read(12000).decode("utf8", errors="replace"),
              "stderr": err.read(6000).decode("utf8", errors="replace"),
              "artifacts": _artifacts(workspace_fd, workspace, started, call_id),
              "permissions": {"task_workspace": "read_write", "conversation_inputs": "read_only",
                              "service_source": "not_mounted",
                              "shared_assets": "authorized task copies only", "host_network": "unavailable; use production tools",
                              "gpu": "use production tools"},
              "artifact_scope": "task-authored intermediate data; never simulator or official GT"}
        with open_write(workspace_fd, call_id + '.receipt.json') as receipt:
            receipt.write(json.dumps(result, ensure_ascii=False, indent=2).encode('utf8'))
    return result
