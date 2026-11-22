"""Compare S2 repair candidates without changing the production acceptance contract."""
import json
import logging
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError

from storage.files import safe_path
from storage.scene_jobs.repair_history import repair_history


def capture_ref(task_id, revision, token):
    """用真实采集令牌区分同一工作项的重试。"""
    return {'task_id': task_id, 'revision': revision, 'token': token}


def choose_baseline(history):
    """只沿已确认有改善的链推进，并保留已经通过整体验收的版本。"""
    base = None
    accepted = False
    for row in history:
        ref = capture_ref(row['task_id'], row['revision'], row['token'])
        progress = row['progress'] or {}
        if row['passed'] is True:
            base, accepted = ref, True
        elif not accepted and ((base is None and progress.get('decision') in (None, 'initial')) or
                (base is not None and progress.get('decision') == 'advance' and progress.get('baseline') == base)):
            base = ref
    return base


def load_baseline(store, task, acceptance):
    """只读取有归属的既有原生图与回执；历史缺失时仍照常验收当前场景。"""
    if not acceptance.get('capture_token') or not acceptance.get('scene_brief'):
        return None
    base = None
    try:
        history = repair_history(store.repo.engine, task, acceptance)
        base = choose_baseline(history)
        if base is None:
            return {'ref': None, 'round': 1}
        result = {'ref': base, 'round': len(history) + 1}
        folder = safe_path(store.root, f"{base['task_id']}/revision_{base['revision']}")
        receipt = json.loads(safe_path(folder, 'scene_acceptance.json', must_exist=True).read_text(encoding='utf8'))
        versions = receipt.get('evidence_versions', {})
        if receipt.get('capture_token') != base['token']:
            return result
        images = []
        for name in sorted(versions):
            path = Path(name)
            if path.parent.as_posix() != 'capture' or not path.match('view_*.png') or not path.stem[5:].isdigit():
                continue
            image = safe_path(folder, name, must_exist=True)
            stat = image.stat()
            if versions[name] != [stat.st_size, stat.st_mtime_ns]:
                return result
            images.append(image)
        if images:
            result.update(receipt=receipt, images=images)
        return result
    except (OSError, ValueError, SQLAlchemyError):
        logging.getLogger(__name__).warning('Repair comparison history unavailable for %s', task['id'], exc_info=True)
        return {'ref': base, 'round': None}


def relation_states(receipt):
    """用对象关系的语义身份对齐两次物理检查，保留未知项。"""
    return {(r['type'], r['source'], r['target']): r['passed']
            for r in receipt.get('native_relation_checks', [])
            if all(isinstance(r.get(k), str) for k in ('type', 'source', 'target')) and type(r.get('passed')) is bool}


def native_changes(before, after):
    """区分明确的状态翻转与仅在本轮首次报告的问题。"""
    old, new = relation_states(before), relation_states(after)
    previous_issues = set(before.get('physical_issues', []))
    return {
        'fixed': sorted(key for key in old.keys() & new.keys() if not old[key] and new[key]),
        'regressed': sorted(key for key in old.keys() & new.keys() if old[key] and not new[key]),
        'coverage_changed': old.keys() != new.keys(),
        'first_reported': [issue for issue in dict.fromkeys(after.get('physical_issues', [])) if issue not in previous_issues],
    }


def comparison_prompt(base, acceptance):
    """给现有视觉审查附上基线图顺序与待核实差异，不另起模型调用。"""
    changes = native_changes(base['receipt'], acceptance)
    reference = base['ref']
    return ('\n以上全部当前采集图及设计参考图已列完；随后附上的图全部来自修复基线，仅用于前后比较。'
            '当前场景的验收和 life_review 仍只依据当前回执与当前实拍。基线图顺序：'
            + json.dumps([p.name for p in base['images']]) +
            '\n基线原生问题：' + json.dumps(base['receipt'].get('physical_issues', []), ensure_ascii=False) +
            '\n前后原生差异：' + json.dumps(changes, ensure_ascii=False) +
            '\n另外返回 repair_comparison，含 baseline_token=' + json.dumps(reference['token']) +
            '，complete（已检查改动及受影响区域），findings 数组。每项含 kind（fixed/regressed/preexisting/uncertain）、'
            'before_view、after_view（各自实际视图文件名）、observation（具体前后事实）。'
            'first_reported 中的每一项如果基线已可见同一缺陷，用 preexisting 并附 first_reported_index（从0开始）。'
            '视角或遮挡使结论不可靠时用 uncertain。逐项辨别新发现与新引入，保留独立改进。'
            '当前实拍中可确认且影响任务的缺陷也须体现在当前场景的 acceptable 和 issues 中。')


def repair_decision(base, candidate, acceptance, review, current_views):
    """验证对照依据，回归或证据不足时继续从保留的版本修复。"""
    previous = base['ref']
    result = {'candidate': candidate, 'baseline': previous, 'round': base['round']}
    if review['passed']:
        return {**result, 'decision': 'accepted', 'next_base': candidate}
    if not base.get('images'):
        decision = 'initial' if previous is None and base['round'] == 1 else 'uncompared'
        return {**result, 'decision': decision, 'next_base': candidate if decision == 'initial' else previous}
    before = base['receipt'].get('scene_brief') or {}
    after = acceptance.get('scene_brief') or {}
    if any(before.get(k) != after.get(k) for k in ('id','plan_path','plan_version')):
        return {**result, 'decision': 'uncompared', 'next_base': None}
    native = native_changes(base['receipt'], acceptance)
    comparison = review.get('repair_comparison')
    if not isinstance(comparison, dict) or comparison.get('baseline_token') != previous['token']:
        comparison = {}
    rows = comparison.get('findings')
    old_views = {p.name for p in base['images']}
    validated = [r for r in rows if isinstance(r, dict) and r.get('kind') in ('fixed','regressed','preexisting','uncertain')
                 and isinstance(r.get('before_view'), str) and r['before_view'] in old_views
                 and isinstance(r.get('after_view'), str) and r['after_view'] in current_views
                 and isinstance(r.get('observation'), str) and r['observation'].strip()] if isinstance(rows, list) else []
    kinds = {r['kind'] for r in validated}
    old_findings = {r['first_reported_index'] for r in validated if r['kind'] == 'preexisting'
                    and type(r.get('first_reported_index')) is int}
    comparable = (comparison.get('complete') is True and isinstance(rows, list) and len(rows) == len(validated)
                  and not native['coverage_changed'] and 'uncertain' not in kinds
                  and set(range(len(native['first_reported']))) <= old_findings)
    decision = 'keep_baseline'
    if native['regressed'] or 'regressed' in kinds:
        decision = 'rollback'
    elif comparable and (native['fixed'] or 'fixed' in kinds) and base['receipt'].get('passed') is not True:
        decision = 'advance'
    elif not comparable:
        decision = 'uncompared'
    return {**result, 'decision': decision, 'next_base': candidate if decision == 'advance' else previous,
            'native': native, 'findings': validated}
