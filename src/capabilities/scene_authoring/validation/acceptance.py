"""Fail-closed operator admission for realistic, physically plausible scene production.

Native measurements and visual opinions remain separate. Conservative bounds
flag potential contact for review. Critical unresolved contact blocks acceptance.
"""
import json
import hashlib
import math
from pathlib import Path

from capabilities.scene_authoring.geometry.relations import native_part_bounds
from capabilities.scene_authoring.geometry.relations import overlap_unresolved
from capabilities.scene_authoring.geometry.relations import transported_mesh_bounds
from capabilities.scene_authoring.validation.policy import policy_for, structure, attachment
from capabilities.scene_authoring.validation.preflight import inspect_program
from capabilities.scene_authoring.validation.diagnostics import ScenePreflightError, format_issue
from capabilities.scene_authoring.validation.review_reference import facing_observation_issue
from capabilities.scene_authoring.validation.support_relations import (
    native_support, relation_requires_measurement, support_check, support_path, visual_relation_indices,
)
from storage.files import safe_path

SCENE_ACCEPTANCE_POLICY_VERSION = 3

VISUAL_CHECKS = {'object_identity', 'realistic_scale', 'support_and_attachment',
                 'functional_layout', 'access_and_clearance'}


# 按显式执行模式或既有配置读取场景验收策略。
def _policy_for_mode(root, execution_mode):
    return policy_for(root) if execution_mode is None else policy_for(root, execution_mode)


# 要求审查引用真实采集视图且具有有效观察内容，不以文字长度替代证据。
def captured_observation(row, frames):
    """A real view and useful text are required; verbosity is not evidence."""
    return (isinstance(row,dict) and isinstance(row.get('view'),str) and row['view'] in frames and
            isinstance(row.get('observation'),str) and bool(row['observation'].strip()))








# 按启用的生产策略预检场景，阻断不满足准入条件的程序。
def admit_program(root, program, *, allow_generated=False, execution_mode=None):
    policy = _policy_for_mode(root, execution_mode)
    if not policy:
        return {'enforced': False, 'passed': None, 'evaluation_status': 'not_evaluated'}
    report = inspect_program(root, program, policy=policy, allow_generated=allow_generated)
    if not report['passed']:
        raise ScenePreflightError(report)
    return report


# 综合真实采集、物理、几何和逐视图审查，生成生产场景验收结论。
def capture_acceptance(root, program, report, evidence, review, capture, execution_mode=None):
    """Require actual native capture, physics, geometry and per-view life review."""
    policy = _policy_for_mode(root, execution_mode)
    if execution_mode == 'exploration' or not policy:
        return {
            'enforced': False,
            'passed': None,
            'evaluation_status': 'not_evaluated',
            'execution_mode': execution_mode or 'exploration',
        }
    issues = []
    physical_advisories = []
    admission={}
    try:admission=admit_program(root, program, execution_mode=execution_mode)
    except ScenePreflightError as error:
        admission = error.report
        issues.extend(format_issue(row) for row in admission['issues'] if row['blocking'])
    except (ValueError, OSError, KeyError, TypeError) as error:issues.append(str(error))
    physics = evidence.get('physics', {})
    if report.get('status') != 'captured' or report.get('renderable') is not True or evidence.get('origin') != 'isaac_native':
        issues.append('Actual native rendered capture is required')
    if not report.get('token') or evidence.get('token') != report.get('token'):
        issues.append('Native capture evidence must bind this exact capture token')
    if (physics.get('stable') is not True or not isinstance(physics.get('steps'), int) or physics['steps'] < 60
            or not isinstance(physics.get('dt'), (int,float)) or not math.isfinite(physics['dt']) or physics['dt'] <= 0):
        issues.append('A stable native physics rollout of at least 60 steps is required')
    actions=[row for row in program.get('interactions',program.get('affordances',[])) if row.get('action')=='apply_force']
    if actions:
        interaction=evidence.get('interaction',{})
        results={row.get('id'):row for row in interaction.get('action_results',[]) if isinstance(row,dict)}
        for action in actions:
            result=results.get(action['id'],{})
            if (interaction.get('validated') is not True or result.get('success') is not True or
                    result.get('action')!='apply_force' or result.get('object_id')!=action['object_id'] or
                    any(not isinstance(result.get(key),str) or not (Path(capture)/result[key]).is_file()
                        for key in ('trajectory_file','before_image','after_image'))):
                issues.append(action['id']+': native interaction needs a passing trajectory and before/after evidence')
    entities = evidence.get('entities', {})
    boxes = {}
    executed_parts = {}
    mesh_bound_checks=[];mesh_versions={}
    dependencies={row.get('entity_id'):row for row in evidence.get('asset_dependencies',[]) if isinstance(row,dict)}
    for obj in program['objects']:
        row = entities.get(obj['id'], {})
        box = row.get('world_aabb', {})
        lo, hi = box.get('min', []), box.get('max', [])
        if (len(lo) != 3 or len(hi) != 3 or not all(isinstance(v,(int,float)) and math.isfinite(v) for v in [*lo,*hi])
                or any(hi[i] <= lo[i] for i in range(3)) or not isinstance(row.get('collision'),dict) or not row['collision'].get('paths')):
            issues.append(obj['id'] + ': native bounds/collider receipt missing');continue
        boxes[obj['id']] = (lo,hi)
        executed_parts[obj['id']] = native_part_bounds(row)
        if obj.get('kind')=='mesh' and obj.get('asset_id'):
            file=Path(root)/'generated_assets'/obj['asset_id']/'mesh.json'
            try:
                tight=transported_mesh_bounds(row,dependencies.get(obj['id'],{}),json.loads(file.read_text()))
                if tight and all(tight[0][i]>=lo[i]-.002 and tight[1][i]<=hi[i]+.002 for i in range(3)):
                    boxes[obj['id']]=tight
                    mesh_versions[str(file)]=[file.stat().st_size,file.stat().st_mtime_ns]
                    mesh_bound_checks.append({'object_id':obj['id'],'min':tight[0],'max':tight[1],
                        'basis':'all transported source vertices under final native world transform; overlap exclusion only'})
            except (OSError,ValueError,TypeError,KeyError):pass
    by_id = {obj['id']: obj for obj in program['objects']}
    brief = admission.get('scene_brief')
    brief_relations = (brief or {}).get('relations', [])
    support_hops = {child for relation in brief_relations if relation['type'] == 'supported_by'
                    for child in support_path(by_id, relation['source'], relation['target'])[:-1]}
    mount_sources = {row['source'] for row in brief_relations if row['type'] == 'mounted_to'}
    verified_mounts = set()
    support_states = {}
    unresolved_relations = set()
    for obj in program['objects']:
        if obj['id'] not in boxes or (structure(obj) and obj['id'] not in support_hops | mount_sources):
            continue
        lo,hi = boxes[obj['id']]
        try:mount = attachment(obj, by_id)
        except ValueError as error:issues.append(str(error));mount = None
        if mount:
            target = boxes.get(mount['target'])
            axis = mount['axis']
            if target:
                face = lo if mount['object_face'] == 'min' else hi
                target_face = target[0] if mount['target_face'] == 'min' else target[1]
                touching = abs(face[axis] - target_face[axis]) <= .04
                inside = all(min(hi[i], target[1][i]) - max(lo[i], target[0][i]) > .005 for i in range(3) if i != axis)
                if touching and inside:
                    verified_mounts.add((obj['id'], mount['target']));continue
            pair = (obj['id'], mount['target'])
            if not target:
                issues.append('/'.join(pair) + ': native mount target evidence is missing')
            elif any(max(lo[i], target[0][i]) - min(hi[i], target[1][i]) > .04 for i in range(3)):
                issues.append('/'.join(pair) + ': a measured gap separates the mount from its declared target')
            else:
                unresolved_relations.add(pair)
                physical_advisories.append('/'.join(pair) + ': static mounting is unverified by bounds. Check native or visible attachment')
            continue
        support = obj['support']
        pair = (obj['id'], support)
        passed, detail = native_support(*pair, boxes, entities, executed_parts)
        if passed is False or (support != 'ground' and support not in boxes):
            issues.append('/'.join(pair) + ': ' + detail)
        elif passed is None:
            physical_advisories.append('/'.join(pair) + ': ' + detail)
        support_states[pair] = passed
    for i, left in enumerate(program['objects']):
        for right in program['objects'][i+1:]:
            if left['id'] not in boxes or right['id'] not in boxes:continue
            if structure(left) and structure(right):continue
            pair = (left['id'], right['id'])
            if pair in verified_mounts or pair[::-1] in verified_mounts:continue
            left_parts = executed_parts.get(left['id']) or [boxes[left['id']]]
            right_parts = executed_parts.get(right['id']) or [boxes[right['id']]]
            if overlap_unresolved(left_parts, right_parts):
                label = left['id'] + '/' + right['id']
                physical_advisories.append(label + ': conservative bounds overlap. Check actual contact, clearance or obstruction where the task depends on it')
    native_relation_checks = []
    action_objects = {row.get('object_id') for row in actions}
    for index, relation in enumerate(brief_relations):
        kind = relation['type']
        if kind not in {'supported_by', 'mounted_to'}:
            continue
        source, target = relation['source'], relation['target']
        if kind == 'supported_by':
            passed, hops = support_check(by_id, source, target, support_states)
        else:
            pair = (source, target)
            passed = None if pair in unresolved_relations else pair in verified_mounts
            hops = []
        affected = {source, target} | {obj for hop in hops for obj in (hop['source'], hop['target'])}
        needs_native = relation_requires_measurement(relation) or any(
            obj in action_objects or by_id.get(obj, {}).get('dynamic') is True for obj in affected)
        visual_required = passed is None and not needs_native
        if passed is False or (passed is None and needs_native):
            issues.append(f'Scene brief {kind} relation lacks required native evidence: {source} -> {target}')
        native_relation_checks.append({
            'index': index, 'type': kind, 'source': source, 'target': target, 'passed': passed,
            'evidence': 'capture/evidence.json', 'support_hops': hops,
            **({'advisory': True, 'visual_check_required': True} if visual_required else {}),
            'basis': 'native world bounds, support surfaces and directed static mounts',
        })
    visual_indices = visual_relation_indices(brief, native_relation_checks) if brief else set()
    physical_issues=list(issues)
    frames = {row.get('frame_id') + '.png' for row in evidence.get('frames', []) if isinstance(row.get('frame_id'), str)}
    frames = {name for name in frames if (Path(capture)/name).is_file()}
    for result in evidence.get('interaction',{}).get('action_results',[]):
        if not isinstance(result,dict):continue
        for key in ('before_image','after_image'):
            name=result.get(key)
            if isinstance(name,str) and Path(name).name==name and (Path(capture)/name).is_file():frames.add(name)
    expected = {'view_' + str(i) + '.png' for i in range(len(program.get('cameras', [])))}
    if not expected or not expected <= frames:
        issues.append('Every planned camera must have an actual captured view for production acceptance')
    visual_pending = bool(physical_issues) and review.get('visual_review_skipped') is True
    checks = review.get('life_review', {}).get('checks', [])
    if visual_pending:
        issues.append('Visual review not assessed: repair the physical precheck and capture again')
    for name in ([] if visual_pending else sorted(VISUAL_CHECKS)):
        rows = [row for row in checks if isinstance(row,dict) and row.get('check') == name]
        if len(rows) != 1 or rows[0].get('passed') is not True:
            issues.append('Visual/life check failed or missing: ' + name);continue
        observations = rows[0].get('evidence', [])
        if not observations or any(not captured_observation(item,frames) for item in observations):
            issues.append('Life check lacks a concrete captured-view observation: ' + name)
    style=admission.get('scene_style','realistic_assets')
    brief = admission.get('scene_brief')
    frozen_file = Path(capture).parent / 'scene_admission.json'
    if brief and frozen_file.is_file():
        frozen = json.loads(frozen_file.read_text(encoding='utf-8')).get('scene_brief')
        if not frozen or frozen.get('plan_version') != brief.get('plan_version'):
            issues.append('Scene brief changed during production; start a new capture for the revised brief')
    if brief and not visual_pending:
        alignment = review.get('brief_alignment', {})
        if alignment.get('passed') is not True or alignment.get('hard_requirements_preserved') is not True:
            issues.append('Captured scene does not satisfy the saved scene brief and protected requirements')
        observed = alignment.get('observations', [])
        if not observed or any(not captured_observation(row,frames) for row in observed):
            issues.append('Scene brief alignment needs actual per-view observations')
        if not expected <= {row['view'] for row in observed if captured_observation(row,frames)}:
            issues.append('Scene brief alignment must account for every captured camera view')
        if alignment.get('unresolved') != []:
            issues.append('Scene brief has unresolved spatial or functional contradictions')
        from capabilities.scene_authoring.contracts.requirements import scene_requirements
        for key, expected_indices in (('requirement_checks', {row['index'] for row in scene_requirements(brief)}),
                                     ('relation_checks', visual_indices)):
            checks = alignment.get(key, [])
            # Native support and attachment findings are recorded separately.
            # A brief requirement for actual contact still needs positive evidence here.
            relevant=[row for row in checks if isinstance(row,dict) and type(row.get('index')) is int and
                      row['index'] in expected_indices] if isinstance(checks,list) else []
            # A checked incidental layout detail can be advisory. Hard requirements
            # and declared clearance must have positive task evidence.
            def advisory_relation(row):
                return (key == 'relation_checks' and row.get('advisory') is True
                        and brief['relations'][row['index']]['type'] not in {'clearance', 'supported_by', 'mounted_to'}
                        and not brief['relations'][row['index']].get('expected_direction'))

            if (not isinstance(checks, list) or len(relevant) != len(expected_indices) or
                    {row.get('index') for row in relevant} != expected_indices or
                    any((row.get('passed') is not True and not advisory_relation(row))
                        or not captured_observation(row, frames) for row in relevant)):
                issues.append('Scene brief needs complete captured evidence for ' + key)
            if key == 'relation_checks':
                for row in relevant:
                    relation = brief['relations'][row['index']]
                    if relation['type'] in {'supported_by', 'mounted_to'} and row.get('view') not in expected:
                        issues.append(f"Scene brief relation {row['index']} needs a current planned camera view")
                    if relation['type'] == 'facing' and not advisory_relation(row):
                        issue = facing_observation_issue(relation, row, evidence.get('frames', []))
                        if issue:
                            issues.append(issue)
    expected_status='matches_declared_geometry' if style=='simplified_geometry' else 'visually_plausible'
    if not visual_pending and (review.get('acceptable') is not True or review.get('visual_realism', {}).get('status') != expected_status):
        issues.append('Captured views failed appearance/semantic acceptance')
    files = [Path(capture).parent/'program.json',Path(capture)/'report.json',Path(capture)/'evidence.json']
    for entity in entities.values():
        geometry = entity.get('geometry_evidence') or {}
        if geometry.get('source') != 'native_flattened_usd':
            continue
        name = geometry.get('file')
        if not isinstance(name, str) or Path(name).name != name:
            issues.append('Native geometry receipt needs a capture-local export')
            continue
        file = Path(capture) / name
        if not file.is_file() or file.is_symlink():
            issues.append('Native geometry export is missing')
            continue
        with file.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != geometry.get('sha256'):
                issues.append('Native geometry export changed after measurement')
        files.append(file)
    for frame in evidence.get('frames', []):
        if isinstance(frame, dict) and isinstance(frame.get('frame_id'), str) and Path(frame['frame_id']).name == frame['frame_id']:
            files.extend(Path(capture)/(frame['frame_id']+suffix)
                         for suffix in ('.png', '.json', '_depth.npy', '_semantic.npy', '_instance.npy'))
    for action in evidence.get('interaction', {}).get('action_results', []):
        if not isinstance(action, dict):
            continue
        recording = action.get('recording') or {}
        for name in (action.get('trajectory_file'), action.get('before_image'), action.get('after_image'),
                     recording.get('timeline_file'), recording.get('video')):
            if isinstance(name, str) and Path(name).name == name:
                files.append(Path(capture) / name)
    versions = {file.relative_to(Path(capture).parent).as_posix():[file.stat().st_size,file.stat().st_mtime_ns] for file in files if file.is_file()}
    return {'schema':'benchforge.scene-acceptance/v1','policy_version':SCENE_ACCEPTANCE_POLICY_VERSION,
            'enforced':True,'passed':not issues,'issues':issues,
            'physical_issues':physical_issues,'physical_advisories':physical_advisories,'visual_status':'not_assessed' if visual_pending else 'assessed',
            'native_relation_checks':native_relation_checks,
            'mesh_bound_checks':mesh_bound_checks,'mesh_versions':mesh_versions,
            'scene_id':program.get('scene_id'),'capture_token':report.get('token'),'evidence_versions':versions,
            'scene_style':style,'geometry_exception':admission.get('geometry_exception'),'scene_brief':brief,
            'physical_scope':'native settling and collider receipts; conservative contact, support and clearance leads are evaluated using capture evidence',
            'life_scope':'per-view model visual judgment, not measured truth or human certification',
            'on_failure':'retain all failed evidence; gather independent physical and visual issues, batch-preflight a complete repair candidate, then recapture within budget; never export or mark complete'}


# 校验复评绑定的原有证据版本，在发布复评结果前再次核对。
def verify_capture_versions(directory, versions):
    if not isinstance(versions, dict) or not {
        'program.json', 'capture/report.json', 'capture/evidence.json'
    } <= versions.keys():
        raise ValueError('Scene acceptance is missing a required native evidence version')
    for relative, version in versions.items():
        try:
            path = safe_path(directory, relative, must_exist=True)
            stat = path.stat()
        except OSError as error:
            raise ValueError('Frozen scene evidence is missing: ' + relative) from error
        if version != [stat.st_size, stat.st_mtime_ns]:
            raise ValueError('Scene changed since acceptance; review the original frozen native evidence')


# 要求场景已有通过的生产验收，并检查简报和冻结证据版本仍然有效。
def require_accepted(root, directory, execution_mode=None):
    policy = _policy_for_mode(root, execution_mode)
    if not policy:return
    directory=Path(directory)
    file=directory/'scene_acceptance.json'
    receipt=json.loads(file.read_text(encoding='utf-8')) if file.is_file() else {}
    if receipt.get('passed') is not True or receipt.get('enforced') is not True:
        raise ValueError('Scene has no passing physical and life acceptance; do not export, select or mark complete')
    if policy.get('require_scene_brief') and not receipt.get('scene_brief'):
        raise ValueError('Scene has no saved brief and capture alignment acceptance')
    verify_capture_versions(directory, receipt.get('evidence_versions'))
    for declaration in (receipt.get('geometry_exception'), receipt.get('scene_brief')):
        if not declaration:continue
        plan=Path(declaration['plan_path'])
        if not plan.is_file() or declaration.get('plan_version')!=[plan.stat().st_size,plan.stat().st_mtime_ns]:
            raise ValueError('Declared scene simplification changed; review the new plan and capture again')
    for name,version in receipt.get('mesh_versions',{}).items():
        path=Path(name)
        if not path.is_file() or [path.stat().st_size,path.stat().st_mtime_ns]!=version:
            raise ValueError('Transported mesh changed since acceptance; recapture before production')
