"""Small JSON artifacts with atomic replacement for concurrent readers.

Each file update is atomic. A workflow still needs an execution lock to
coordinate changes to several files and external services.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

if __package__:
    from service_access.processes import terminate_process_tree
    from storage.json_io import atomic_text as atomic_text, write_json, write_jsonl
else:
    from processes import terminate_process_tree
    from json_io import atomic_text as atomic_text, write_json, write_jsonl


# 读取兼容 UTF-8 BOM 的 JSON 文件并返回解析值。
def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


# 逐行解析非空 JSONL 记录，兼容 UTF-8 BOM。
def read_jsonl(path):
    with Path(path).open(encoding="utf-8-sig") as stream:
        return [json.loads(line) for line in stream if line.strip()]


read = read_json
rows = read_jsonl
write = write_json
jsonl = write_jsonl


# 启动配置指定的 Python 脚本，记录命令及标准输出错误，并检查超时和退出码。
def run(script, argv, directory, *, config=None, env=None, timeout=600, name=None, accepted=(0,)):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    stem = name or Path(script).stem
    command = list((config or {}).get('python_command', [sys.executable])) + [str(script), *map(str, argv)]
    write(directory/(stem+'.command.json'), {'command':command})
    with (directory/(stem+'.stdout.log')).open('w',encoding='utf-8') as out, (directory/(stem+'.stderr.log')).open('w',encoding='utf-8') as err:
        kwargs = {'start_new_session': True} if os.name != 'nt' else {
            'creationflags': subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP,
        }
        result = subprocess.Popen(
            command, cwd=directory,
            env={**os.environ, 'PYTHONUTF8': '1', **(env or {})},
            stdout=out, stderr=err, **kwargs,
        )
        try:result.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            terminate_process_tree(result)
            raise
    write(directory/(stem+'.exit.json'), {'exit_code':result.returncode})
    if result.returncode not in accepted:
        tail=(directory/(stem+'.stderr.log')).read_text(encoding='utf-8')[-3000:]
        tail+=(directory/(stem+'.stdout.log')).read_text(encoding='utf-8')[-2000:]
        raise RuntimeError(f'{stem} exited {result.returncode}; logs: {directory}; {tail}')
    return result.returncode


# 按指定字段建立记录索引，遇到缺失或重复身份立即报错。
def unique(values, key='id'):
    result={}
    for row in values:
        identity=str(row[key])
        if not identity or identity in result:
            raise ValueError(f'Missing or duplicate {key}: {identity}')
        result[identity]=row
    return result


# 按照 RFC 6901 JSON 指针遍历对象或数组，处理转义后的键名。
def select(raw, pointer):
    if pointer and not pointer.startswith('/'):
        raise ValueError('Selectors use RFC 6901 JSON pointers')
    value = raw
    for part in pointer.split('/')[1:]:
        part = part.replace('~1', '/').replace('~0', '~')
        value = value[int(part)] if isinstance(value, list) else value[part]
    return value
