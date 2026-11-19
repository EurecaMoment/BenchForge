"""Pipeline authority lives in PostgreSQL; task files are readable projections."""

from pathlib import Path
from database.engine import engine_for
from service_access.access_scope import current_access
from storage.access_documents import read_document, run_document_key, write_document
from storage.artifacts import read, write
from storage.operation_outputs import logical_value, physical_value


class RunDocuments:
    # 绑定运行根、当前访问作用域及稳定文档键，并初始化乐观并发版本缓存。
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.access = current_access()
        self.engine = engine_for() if self.access else None
        self.key = run_document_key(self.root)
        self.revisions = {}

    # 从可信数据库或本地文件恢复运行文档，记录版本并还原当前私有执行路径。
    def load(self, name, default=None):
        if self.access is None:
            file = self.root / name
            return read(file) if file.is_file() else default
        row = read_document(self.engine, self.access.scope, self.key, name)
        self.revisions[name] = row['revision'] if row else 0
        return physical_value(row['document']) if row else default

    # 按已读版本比较后保存数据库文档，同时写出运行目录中的文件副本。
    def save(self, name, document):
        if self.access is not None:
            if name not in self.revisions:
                self.load(name)
            row = write_document(self.engine, self.access.scope, self.key, name, logical_value(document),
                                 expected_revision=self.revisions[name])
            self.revisions[name] = row['revision']
        write(self.root / name, document)
