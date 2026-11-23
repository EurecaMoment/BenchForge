"""Structured, complete authoring feedback shared by preflight and admission."""
from collections import Counter


REPAIR_GUIDANCE = (
    'Read the complete issue list and skipped checks before editing. Repair all evidenced, '
    'related defects in one candidate, using repair_groups and support dependencies. Preserve '
    'user hard requirements, intended identities, valid assets/actions and unaffected objects. '
    'Warnings are questions to investigate, not mandatory geometry changes: bounds do not '
    'establish mesh collision, semantic pose, visual quality or physical contact. Do not shrink, '
    'rotate, delete or replace an object solely to silence a heuristic. Re-run this preflight '
    'on the complete revised program, then capture and inspect actual native/visual evidence. '
    'A passing preflight is not scene acceptance. Skipped or pending checks are not passes.'
)


class ScenePreflightError(ValueError):
    code = 'scene_preflight_failed'

    # 保留完整预检报告，并在异常中列出阻断问题与修复指引。
    def __init__(self, report):
        self.report = report
        errors = [row for row in report['issues'] if row['blocking']]
        super().__init__('Scene admission failed; batch preflight found '
                         f'{len(errors)} blocking issue(s):\n' + '\n'.join(
                             format_issue(row) for row in errors
                         ) + '\n' + REPAIR_GUIDANCE)


# 将结构化问题格式化为含对象和字段路径的简短说明。
def format_issue(issue):
    objects = 'object ' + ', '.join(issue['object_ids']) + ' ' if issue.get('object_ids') else ''
    return f"{objects}{issue['path'] or '/'}: {issue['message']}"


# 按支撑或固定关联归并受影响对象，便于整体复核而不指定修改方式。
def repair_groups(issues, objects):
    """Group affected support/mount components without prescribing an edit."""
    parents = {}

    # 用路径压缩查找对象所属的关联组件代表。
    def find(value):
        parents.setdefault(value, value)
        root = value
        while parents[root] != root:
            root = parents[root]
        while parents[value] != value:
            parent = parents[value]
            parents[value] = root
            value = parent
        return root

    # 合并两个相互关联对象所在的组件。
    def join(left, right):
        parents[find(right)] = find(left)

    known = {obj['id'] for obj in objects
             if isinstance(obj, dict) and isinstance(obj.get('id'), str)}
    for obj in objects:
        if not isinstance(obj, dict) or not isinstance(obj.get('id'), str):
            continue
        find(obj['id'])
        support = obj.get('support')
        if isinstance(support, str) and support in known:
            join(obj['id'], support)
        metadata = obj.get('metadata')
        mount = metadata.get('spatial_attachment') if isinstance(metadata, dict) else None
        target = mount.get('target') if isinstance(mount, dict) else None
        if isinstance(target, str) and target in known:
            join(obj['id'], target)
    for issue in issues:
        ids = issue.get('object_ids', [])
        for value in ids:
            find(value)
        for value in ids[1:]:
            join(ids[0], value)
    groups = {}
    for issue in issues:
        ids = issue.get('object_ids', [])
        section = issue['path'].split('/')[1] if issue['path'].startswith('/') else ''
        key = ('objects', find(ids[0])) if ids else ('scene', section)
        group = groups.setdefault(key, {'issue_ids': [], 'object_ids': [], 'paths': []})
        group['issue_ids'].append(issue['id'])
        if issue['path'] not in group['paths']:
            group['paths'].append(issue['path'])
    for key, group in groups.items():
        if key[0] == 'objects':
            group['object_ids'] = sorted(value for value in parents if find(value) == key[1])
    return list(groups.values())


class SceneDiagnostics:
    # 初始化问题、跳过检查及已执行检查的记录列表。
    def __init__(self):
        self.issues = []
        self.skipped = []
        self.checked = []

    # 追加带证据来源、严重度和可选对象标识的诊断问题。
    def add(self, code, path, message, *, basis, severity='error', object_ids=(), **evidence):
        issue = {'code': code, 'path': path, 'message': message, 'basis': basis,
                 'severity': severity, 'blocking': severity == 'error'}
        if object_ids:
            issue['object_ids'] = list(object_ids)
        if evidence:
            issue['evidence'] = evidence
        self.issues.append(issue)

    # 记录因依赖不可用等原因未执行的检查及解释。
    def skip(self, check, path, reason, **evidence):
        row = {'check': check, 'path': path, 'reason': reason, 'status': 'not_evaluated'}
        if evidence:
            row['evidence'] = evidence
        self.skipped.append(row)

    # 独立执行一个检查，收集常见输入错误并记录检查是否完成。
    def attempt(self, check, path, function, *, object_ids=(), basis='declared_program'):
        try:
            value = function()
        except (ValueError, OSError, KeyError, TypeError, IndexError) as error:
            evidence = getattr(error, 'evidence', None)
            self.add(check, path, str(error), basis=basis, object_ids=object_ids,
                     **(evidence if isinstance(evidence, dict) else {}))
            return False, None
        self.checked.append({'check': check, 'path': path})
        return True, value

    # 为问题分配稳定编号并汇总预检状态、统计及已检查范围。
    def finish(self, program, *, enforced, **details):
        for index, issue in enumerate(self.issues):
            issue['id'] = f'issue_{index + 1:04d}'
        counts = Counter(row['severity'] for row in self.issues)
        objects = program.get('objects', []) if isinstance(program, dict) else []
        objects = objects if isinstance(objects, list) else []
        return {
            'schema': 'benchforge.scene-diagnostics/v1', 'stage': 'before_capture',
            'authority': 'static program/source-geometry diagnostics; not simulator evidence or GT',
            'enforced': enforced, 'passed': counts['error'] == 0,
            'status': 'blocked' if counts['error'] else 'needs_verification' if self.issues or self.skipped else 'ready_for_capture',
            'summary': {'errors': counts['error'], 'warnings': counts['warning'],
                        'pending': counts['pending'], 'skipped_checks': len(self.skipped)},
            'issues': self.issues, 'skipped_checks': self.skipped, 'checked': self.checked,
            'repair_groups': repair_groups(self.issues, objects),
            'complete_issue_list': True, 'guidance': REPAIR_GUIDANCE,
            'runtime_checks': {'status': 'not_evaluated', 'required': [
                'native adapter compilation, asset contents and live service availability',
                'native settling and contact', 'collision/clearance', 'all planned camera views',
                'visual identity, orientation and appearance', 'declared actions and scene brief alignment',
            ]},
            **details,
        }
