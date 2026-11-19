"""Operator scene exclusions and a lightweight box-proxy admission rule."""
import json
from pathlib import Path
import re
from service_access.scene_service import environment_value

STRUCTURE = re.compile(r'^(?:floor|ceiling|wall(?:_|$)|ground|terrain|pavement|asphalt|roof|road(?:_|$)|bay_line|rug|carpet|window|door(?:_|$))', re.I)
CLASSIFIER_VERSION = 4
EXCLUDED_SCENE = 'This cube/proxy scene was removed by the user and cannot be reused'


# 延迟加载场景设计解析模块，避免模块初始化时形成循环依赖。
def scene_design_parser():
    from capabilities.scene_authoring.contracts import scene_design
    return scene_design


# 读取场景计划中精确绑定的几何简化例外声明。
def planned_geometry(program, policy=None):
    return scene_design_parser().geometry_exception(program, policy)


# 按前景对象的实际表示检查简单盒体占位风险，并标明仍需视觉验收。
def classify(program):
    props = [obj for obj in program.get('objects', []) if isinstance(obj, dict) and not STRUCTURE.search(str(obj.get('id', '')))]
    detailed = [obj for obj in props if obj.get('kind') == 'mesh' or str(obj.get('kind', '')).startswith('office_') or obj.get('kind') == 'mug']
    boxes = [obj for obj in props if obj.get('kind', 'box') in ('box', 'composite', 'bin', 'container')]
    simple = [obj for obj in props if obj.get('kind', 'box') == 'box']
    explicit = [obj for obj in props if re.search(r'cube|立方体', str(obj.get('id', '')) + ' ' + str(obj.get('label', '')), re.I)]
    reason = ('explicit_cube_objects' if explicit else 'foreground_box_proxy' if simple else
              'primitive_box_proxy_scene' if boxes and not detailed else None)
    return {'allowed': reason is None, 'reason': reason, 'classifier_version': CLASSIFIER_VERSION, 'foreground_objects': len(props),
            'simple_boxes': len(simple), 'detailed_objects': len(detailed),
            'cube_objects': [obj.get('id') for obj in explicit],
            'scope': 'program representation check; captured appearance still requires visual acceptance'}


# 结合细分结构与显式几何例外判断计划表示资格，保留分类依据。
def classify_planned(program, policy=None):
    # Detailed multi-part furniture and real open containers remain subject to
    # captured identity/support checks; the old removal classifier is stricter.
    candidate = {**program, 'objects': [obj for obj in program.get('objects', []) if not (
        obj.get('kind') in {'bin', 'container'} or obj.get('kind') == 'composite' and len(obj.get('parts', [])) >= 3 and
        len({tuple(part.get('offset', [0,0,0])) for part in obj.get('parts', [])}) >= 3)]}
    verdict = classify(candidate)
    declaration = planned_geometry(program, policy)
    if declaration:
        remaining = {**candidate, 'objects': [obj for obj in candidate.get('objects', [])
                                            if obj['id'] not in declaration['object_ids']]}
        allowed = classify(remaining)['allowed']
        verdict = {**verdict, 'allowed': allowed, 'geometry_exception': declaration,
                   'scene_style': 'simplified_geometry'}
    else:
        verdict['scene_style'] = 'realistic_assets'
    return verdict


# 读取存在的JSON策略文件，未配置或不存在时返回空策略。
def _read_policy(file):
    path = Path(file) if file else None
    if path is None or not path.is_file():
        return {}
    return json.loads(path.read_text(encoding='utf-8'))


# 按工作区和服务配置选择与生产服务一致的来源策略。
def load_policy(workspace, service_config=None):
    """Load the same policy selected by the BenchForge service config."""
    if service_config:
        from service_access.scene_service import load_service_client_config

        config = load_service_client_config(service_config)
        mode = config['execution_mode']
        file = config.get('scene_policy_path')
    else:
        mode = environment_value('BENCHFORGE_EXECUTION_MODE')
        file = environment_value('BENCHFORGE_SCENE_POLICY')
        if not file:
            candidate = Path(workspace) / 'scene-library' / 'policy.json'
            file = candidate if candidate.is_file() else None
        if not mode:
            mode = 'production' if file else 'exploration'

    if mode not in {'production', 'exploration'}:
        raise ValueError('execution_mode must be production or exploration')
    policy = _read_policy(file)
    if mode == 'production' and (not file or not policy.get('enabled') or not policy.get('enforce_realism')):
        raise ValueError('Production execution requires an enabled, realism-enforcing scene policy')
    if mode == 'exploration':
        return {'execution_mode': mode}
    policy['execution_mode'] = mode
    if policy.get('enabled') and policy.get('enforce_realism'):
        policy['require_scene_brief']=True
    return policy


# 使用统一场景设计规则判断来源路径是否位于排除范围。
def excluded_path(policy, value):
    return scene_design_parser().path_is_excluded(value, policy)


# 策略启用时拒绝已排除任务、运行、场景组或来源文件。
def check_source(policy, *, task_id=None, run_id=None, scene_group=None, source_path=None):
    if not policy.get('enabled'):
        return
    if (task_id in policy.get('excluded_tasks', []) or run_id in policy.get('excluded_runs', []) or
            scene_group in policy.get('excluded_scene_groups', []) or source_path and excluded_path(policy, source_path)):
        raise ValueError(EXCLUDED_SCENE)


# 在真实性策略下核验服务验收及固定分区，返回可用于生产的证据回执。
def check_evidence(workspace, record, evidence_path, service_config=None, *, receipts=None, policy=None):
    policy=policy or load_policy(workspace, service_config)
    if not policy.get('enabled') or not policy.get('enforce_realism'):return
    from service_access.production_receipts import SceneReceipts
    receipt = (receipts or SceneReceipts(service_config)).for_record(record)
    split = record.get('split')
    if split and receipt.get('split') and split != receipt['split']:
        raise ValueError('Evidence split conflicts with the service-accepted scene partition')
    return receipt

