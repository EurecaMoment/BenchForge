"""Pipeline authority lives in PostgreSQL; task files are readable projections."""

from pathlib import Path
import hashlib
from database.engine import engine_for
from service_access.access_scope import current_access
from storage.access_documents import read_document, write_document
from storage.artifacts import read, write
from storage.operation_outputs import logical_path, logical_value, physical_value


class RunDocuments:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.access = current_access()
        self.engine = engine_for() if self.access else None
        self.key = hashlib.sha256(str(logical_path(self.root)).encode()).hexdigest()
        self.revisions = {}

    def load(self, name, default=None):
        if self.access is None:
            file = self.root / name
            return read(file) if file.is_file() else default
        row = read_document(self.engine, self.access.scope, self.key, name)
        self.revisions[name] = row['revision'] if row else 0
        return physical_value(row['document']) if row else default

    def save(self, name, document):
        if self.access is not None:
            if name not in self.revisions:
                self.load(name)
            row = write_document(self.engine, self.access.scope, self.key, name, logical_value(document),
                                 expected_revision=self.revisions[name])
            self.revisions[name] = row['revision']
        write(self.root / name, document)
