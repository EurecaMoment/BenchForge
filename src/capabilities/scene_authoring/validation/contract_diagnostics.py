"""Collect independent SceneProgram contract failures without rewriting input."""
from contextlib import contextmanager


class ProgramValidationError(ValueError):
    code = 'scene_contract_invalid'

    # 保留全部场景合同问题，并以对象和字段路径组织异常说明。
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

    # 建立可批量累积场景合同问题的容器。
    def __init__(self):
        self.issues = []

    # 捕获指定字段校验异常，转换成带对象与路径的结构化阻断问题。
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

    # 在指定路径运行校验函数，并返回是否没有新增问题。
    def check(self, path, function, *args, object_id=None, **kwargs):
        before = len(self.issues)
        with self.at(path, object_id=object_id):
            function(*args, **kwargs)
        return len(self.issues) == before

    # 将布尔前置条件转换为可累积的字段校验问题。
    def require(self, path, condition, message, *, object_id=None):
        # 条件不成立时抛出由外围诊断收集器捕获的校验错误。
        def check():
            if not condition:
                raise ValueError(message)
        return self.check(path, check, object_id=object_id)

    # 验证列表及其条目数量范围，再返回可继续独立检查的索引条目。
    def records(self, value, path, maximum, message, *, minimum=0):
        if not self.require(path, isinstance(value, list), message):
            return []
        self.require(path, minimum <= len(value) <= maximum, message)
        return enumerate(value)
