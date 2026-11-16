"""Collect independent SceneProgram contract failures without rewriting input."""
from contextlib import contextmanager


class ProgramValidationError(ValueError):
    code = 'scene_contract_invalid'

    def __init__(self, issues):
        self.issues = issues
        super().__init__('Scene contract failed:\n' + '\n'.join(
            ('object ' + ', '.join(issue['object_ids']) + ' ' if issue.get('object_ids') else '')
            + f"{issue['path'] or '/'}: {issue['message']}" for issue in issues
        ))


class ContractDiagnostics:
    """Each scope is a pure check; failure skips only its dependent statements.

    Callers put unrelated fields/records in separate scopes. The exception
    boundary is deliberately local to data validation, never execution or I/O.
    """

    def __init__(self):
        self.issues = []

    @contextmanager
    def at(self, path, *, object_id=None):
        try:
            yield
        except (ValueError, TypeError, KeyError, IndexError) as error:
            issue = {
                'code': 'invalid_scene_contract', 'path': path,
                'severity': 'error', 'blocking': True,
                'basis': 'scene_contract', 'message': str(error),
            }
            if isinstance(object_id, str):
                issue['object_ids'] = [object_id]
            self.issues.append(issue)

    def check(self, path, function, *args, object_id=None, **kwargs):
        before = len(self.issues)
        with self.at(path, object_id=object_id):
            function(*args, **kwargs)
        return len(self.issues) == before

    def require(self, path, condition, message, *, object_id=None):
        def check():
            if not condition:
                raise ValueError(message)
        return self.check(path, check, object_id=object_id)

    def records(self, value, path, maximum, message, *, minimum=0):
        if not self.require(path, isinstance(value, list), message):
            return []
        self.require(path, minimum <= len(value) <= maximum, message)
        return enumerate(value)
