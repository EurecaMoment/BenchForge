"""Freeze validated task-authored scene data before queueing; never import code or GT."""
import json
from pathlib import Path
from uuid import uuid4
from service_access.runtime_config import environment_value

MAX_PROGRAM_BYTES=1024*1024


# 构建JSON对象并拒绝重复键，避免场景字段被静默覆盖。
def _pairs(pairs):
    result={}
    for key,value in pairs:
        if key in result:raise ValueError('duplicate SceneProgram JSON key: '+key)
        result[key]=value
    return result


# 保留提交身份与模式标识，使冻结源文件快照可被准确追踪。
def submission_identity(value):
    if value is None or 'schema' not in value:
        return value
    # The authored program snapshot and byte count identify the source file.
    return {**value, 'schema': value['schema']}


# 从授权工作区读取有限大小场景JSON快照，校验数字、重复键和资产访问权。
def read_program_document(artifact_root, source_path):
    from service_access.access_scope import current_access, workspace_path
    if not isinstance(source_path,str) or not source_path:raise ValueError('scene_program_path must be a task workspace JSON source_path')
    access = current_access()
    if access is not None:
        path = workspace_path(source_path)
        roots = [Path(access.root_path).resolve()]
    else:
        roots=[(Path(artifact_root)/'operator_workspaces').resolve()]
        configured_root=environment_value('BENCHFORGE_TASK_ROOT')
        if configured_root:
            roots.append(Path(configured_root).expanduser().resolve())
        path=Path(source_path).resolve()
    if not any(path.is_relative_to(root) for root in roots) or path.suffix.lower()!='.json':
        raise ValueError('scene_program_path must be a .json file inside an operator task workspace. '
                         f'Accepted roots: {", ".join(str(root) for root in roots)}. '
                         'Use standard file tools to write or copy the task JSON under a configured task root, '
                         'or use benchforge_task_code and its returned source_path.')
    try:
        if access is not None:
            from storage.confined_files import read_bytes
            raw = read_bytes(access.root_path, path, limit=MAX_PROGRAM_BYTES)
        else:
            with path.open('rb') as stream:raw=stream.read(MAX_PROGRAM_BYTES+1)
    except OSError as exc:raise ValueError('SceneProgram task file unavailable') from exc
    if len(raw)>MAX_PROGRAM_BYTES:raise ValueError('SceneProgram exceeds 1 MiB input limit')
    # 拒绝JSON中的非有限数值常量。
    def invalid_constant(value):raise ValueError('non-finite JSON constant: '+value)
    try:
        program=json.loads(raw.decode('utf-8-sig'),object_pairs_hook=_pairs,parse_constant=invalid_constant)
        json.dumps(program,allow_nan=False)  # Also rejects exponent overflow such as 1e999.
    except (UnicodeError,ValueError,RecursionError) as exc:raise ValueError('invalid SceneProgram JSON: '+str(exc)[:300]) from exc
    if access is not None:
        from capabilities.asset_generation.asset_access import registered_asset_path
        # 递归验证已登记网格资产的访问权，原生资产句柄由专用流程处理。
        def authorize_assets(value):
            if isinstance(value, dict):
                if isinstance(value.get('asset_id'), str) and value['asset_id'].startswith('asset_'):
                    registered_asset_path(artifact_root, value['asset_id'], access=access)
                for key, child in value.items():
                    if key != 'native_asset':
                        authorize_assets(child)
            elif isinstance(value, list):
                for child in value:
                    authorize_assets(child)
        authorize_assets(program)
    return {'schema':'benchforge.program-submission/v1','source_path':str(path),
        'source_bytes':len(raw),'authority':'task-authored scene data; not GT',
        'program':program}


# 为每次调用保存完整且不可变的诊断及其准确输入快照。
def save_preflight_report(artifact_root, submission, report):
    """Keep a complete immutable diagnostic and its exact input per invocation."""
    directory = Path(artifact_root) / 'scene_preflights' / uuid4().hex
    directory.mkdir(parents=True, exist_ok=False)
    result = {**report, 'source_path': submission['source_path'],
              'source_bytes': submission['source_bytes'],
              'report_path': str((directory / 'scene_preflight.json').resolve()),
              'program_snapshot_path': str((directory / 'program.json').resolve())}
    (directory / 'program.json').write_text(
        json.dumps(submission['program'], ensure_ascii=False, allow_nan=False), encoding='utf-8')
    (directory / 'scene_preflight.json').write_text(
        json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2), encoding='utf-8')
    return result


# 读取场景提交并执行批量预检，将完整诊断保存为可审查产物。
def inspect_program_submission(artifact_root, source_path, *, execution_mode=None, inspector=None):
    from capabilities.scene_authoring.validation.preflight import inspect_program
    submission = read_program_document(artifact_root, source_path)
    report = (inspector or inspect_program)(
        artifact_root, submission['program'], execution_mode=execution_mode)
    return save_preflight_report(artifact_root, submission, report)


# 读取并预检场景提交，失败时保留报告并抛出准入异常。
def read_program_submission(artifact_root, source_path, *, execution_mode=None, policy=None,
                            inspector=None, include_preflight=False):
    from capabilities.scene_authoring.validation.preflight import inspect_program, ScenePreflightError
    submission = read_program_document(artifact_root, source_path)
    report = (inspector or inspect_program)(
        artifact_root, submission['program'], execution_mode=execution_mode, policy=policy)
    if not report['passed']:
        raise ScenePreflightError(save_preflight_report(artifact_root, submission, report))
    return {**submission, 'preflight': report} if include_preflight else submission


# 比较提交快照与准备后的场景，记录对象、相机及各分区的精确变更。
def handoff_receipt(submission,program,revision):
    original=submission['program']
    old={o['id']:o for o in original['objects']};new={o['id']:o for o in program['objects']}
    changed=[{'id':key,'fields':sorted(k for k in set(old[key])|set(new[key]) if old[key].get(k)!=new[key].get(k))}
        for key in old.keys()&new.keys() if old[key]!=new[key]]
    return {'schema':'benchforge.program-handoff/v1','source_path':submission['source_path'],
        'authority':submission['authority'],'revision':revision,
        'program_stage':'prepared_for_capture; execution requires capture evidence',
        'first_pass_mode':'validated file snapshot without planner rewrite',
        'submitted_object_count':len(old),'prepared_object_count':len(new),
        'submitted_camera_count':len(original['cameras']),'prepared_camera_count':len(program['cameras']),
        'exact_program_match':original==program,'added_objects':sorted(new.keys()-old.keys()),
        'removed_objects':sorted(old.keys()-new.keys()),'changed_objects':sorted(changed,key=lambda row:row['id']),
        'changed_sections':sorted(k for k in set(original)|set(program) if k!='objects' and original.get(k)!=program.get(k)),
        'notes':['generated assets and native aliases retain existing realization/normalization rules',
                 'submitted scene data stays operator-controlled; inspect captures and submit your own revision',
                 'handoff equality is not simulation, contact, photorealism or benchmark acceptance']}
