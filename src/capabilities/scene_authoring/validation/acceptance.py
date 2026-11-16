"""Fail-closed operator admission for realistic, physically plausible scene production.

Native measurements and visual opinions remain separate. Conservative bound
overlaps are unresolved, not proof of penetration; unresolved scenes cannot ship.
"""
import json
import hashlib
import math
from pathlib import Path
import re

from capabilities.scene_authoring.geometry.relations import native_part_bounds
from capabilities.scene_authoring.geometry.relations import supported_by_parts
from capabilities.scene_authoring.geometry.relations import supported_by_native_parts
from capabilities.scene_authoring.geometry.relations import overlap_unresolved
from capabilities.scene_authoring.geometry.relations import transported_mesh_bounds
from capabilities.scene_authoring.validation.policy import check_references
from capabilities.scene_authoring.validation.policy import policy_for, structure, attachment
from capabilities.scene_authoring.validation.preflight import inspect_program
from capabilities.scene_authoring.validation.diagnostics import ScenePreflightError, format_issue

VISUAL_CHECKS = {'object_identity', 'realistic_scale', 'support_and_attachment',
                 'functional_layout', 'access_and_clearance', 'materials_and_lighting'}


def _policy_for_mode(root, execution_mode):
    return policy_for(root) if execution_mode is None else policy_for(root, execution_mode)


def captured_observation(row, frames):
    """A real view and useful text are required; verbosity is not evidence."""
    return (isinstance(row,dict) and isinstance(row.get('view'),str) and row['view'] in frames and
            isinstance(row.get('observation'),str) and bool(row['observation'].strip()))








def admit_program(root, program, *, allow_generated=False, execution_mode=None):
    policy = _policy_for_mode(root, execution_mode)
    if not policy:
        return {'enforced': False, 'passed': None, 'evaluation_status': 'not_evaluated'}
    report = inspect_program(root, program, policy=policy, allow_generated=allow_generated)
    if not report['passed']:
        raise ScenePreflightError(report)
    return report


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
    props = [obj for obj in program['objects'] if not structure(obj)]
    by_id = {obj['id']: obj for obj in program['objects']}
    verified_mounts = set()
    for obj in props:
        if obj['id'] not in boxes:continue
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
                    verified_mounts.add(frozenset((obj['id'], mount['target'])));continue
            issues.append(obj['id'] + ': static mount has no native connection to its declared target');continue
        support = obj['support']
        if support == 'ground':
            if abs(lo[2]) > .04:issues.append(obj['id'] + ': native geometry floats above or penetrates ground')
        elif support in boxes:
            a,b = boxes[support]
            supported = supported_by_native_parts((lo,hi),entities[support]) if executed_parts.get(support) else supported_by_parts((lo,hi),[(a,b)])
            if not supported:
                issues.append(obj['id'] + ': native support gap or overhang is unresolved')
        else:issues.append(obj['id'] + ': native support evidence missing')
    for i, left in enumerate(program['objects']):
        for right in program['objects'][i+1:]:
            if left['id'] not in boxes or right['id'] not in boxes:continue
            if structure(left) and structure(right):continue
            if frozenset((left['id'], right['id'])) in verified_mounts:continue
            left_parts = executed_parts.get(left['id']) or [boxes[left['id']]]
            right_parts = executed_parts.get(right['id']) or [boxes[right['id']]]
            if overlap_unresolved(left_parts, right_parts):
                issues.append(left['id'] + '/' + right['id'] + ': overlapping native bounds leave mesh intersection unresolved; inspect native geometry/contact evidence before deciding a layout repair')
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
    native_relation_checks=[]
    if brief:
        def relation_passed(relation):
            """Evaluate only the native evidence for this relation.

            A separate physical defect must not make unrelated relations look
            failed in the report. The overall receipt still fails when any
            physical issue remains; this only keeps the per-relation evidence
            honest and actionable.
            """
            source, target = relation['source'], relation['target']
            if source not in boxes:
                return False
            obj = by_id.get(source)
            if not obj:
                return False
            if relation['type'] == 'mounted_to':
                mount = obj.get('metadata', {}).get('spatial_attachment')
                return (isinstance(mount, dict) and mount.get('target') == target and
                        frozenset((source, target)) in verified_mounts)
            if relation['type'] != 'supported_by' or obj.get('support') != target:
                return False
            lo, hi = boxes[source]
            if target == 'ground':
                return abs(lo[2]) <= .04
            if target not in boxes:
                return False
            support_entity = entities.get(target, {})
            if executed_parts.get(target):
                return supported_by_native_parts((lo, hi), support_entity)
            return supported_by_parts((lo, hi), [(boxes[target][0], boxes[target][1])])

        for index,relation in enumerate(brief['relations']):
            if relation['type'] not in {'supported_by','mounted_to'}:continue
            source,target=relation['source'],relation['target']
            passed=relation_passed(relation)
            native_relation_checks.append({'index':index,'type':relation['type'],'source':source,'target':target,
                'passed':passed,'evidence':'capture/evidence.json',
                'basis':'native world bounds, executed support parts or verified static mount; not visual opinion'})
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
                                     ('relation_checks', {i for i,r in enumerate(brief['relations']) if r['type'] not in {'supported_by','mounted_to'}})):
            checks = alignment.get(key, [])
            # Physical relations are already measured above. Visual opinions
            # about hidden shelf bottoms must not replace those native checks.
            relevant=[row for row in checks if isinstance(row,dict) and type(row.get('index')) is int and
                      row['index'] in expected_indices] if isinstance(checks,list) else []
            if (not isinstance(checks, list) or len(relevant) != len(expected_indices) or
                    {row.get('index') for row in relevant} != expected_indices or
                    any(row.get('passed') is not True or not captured_observation(row,frames) for row in relevant)):
                issues.append('Scene brief needs complete captured evidence for ' + key)
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
    return {'schema':'benchforge.scene-acceptance/v1','enforced':True,'passed':not issues,'issues':issues,
            'physical_issues':physical_issues,'visual_status':'not_assessed' if visual_pending else 'assessed',
            'native_relation_checks':native_relation_checks,
            'mesh_bound_checks':mesh_bound_checks,'mesh_versions':mesh_versions,
            'scene_id':program.get('scene_id'),'capture_token':report.get('token'),'evidence_versions':versions,
            'scene_style':style,'geometry_exception':admission.get('geometry_exception'),'scene_brief':brief,
            'physical_scope':'native settling, conservative world bounds, support gap and collider receipts; uncalibrated material parameters',
            'life_scope':'per-view model visual judgment, not measured truth or human certification',
            'on_failure':'retain all failed evidence; gather independent physical and visual issues, batch-preflight a complete repair candidate, then recapture within budget; never export or mark complete'}


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
    for relative, version in receipt.get('evidence_versions', {}).items():
        path = directory / relative
        if (Path(relative).is_absolute() or '..' in Path(relative).parts or path.is_symlink()
                or not path.is_file() or path.resolve() != path
                or version != [path.stat().st_size,path.stat().st_mtime_ns]):
            raise ValueError('Scene changed since acceptance; recapture and review before production')
    if not {'program.json', 'capture/report.json', 'capture/evidence.json'} <= set(receipt.get('evidence_versions', {})):
        raise ValueError('Scene acceptance is missing a required native evidence version')
    for declaration in (receipt.get('geometry_exception'), receipt.get('scene_brief')):
        if not declaration:continue
        plan=Path(declaration['plan_path'])
        if not plan.is_file() or declaration.get('plan_version')!=[plan.stat().st_size,plan.stat().st_mtime_ns]:
            raise ValueError('Declared scene simplification changed; review the new plan and capture again')
    for name,version in receipt.get('mesh_versions',{}).items():
        path=Path(name)
        if not path.is_file() or [path.stat().st_size,path.stat().st_mtime_ns]!=version:
            raise ValueError('Transported mesh changed since acceptance; recapture before production')
