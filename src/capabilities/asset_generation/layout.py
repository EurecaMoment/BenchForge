"""Diffusion layout references guide proposals; they never become simulator GT."""
import json
import math
import shutil
import re
from pathlib import Path
from PIL import Image
from storage.files import write_json
from service_access.progress import record_progress
from service_access.request_context import RunContext


_LAYOUT_OBJECT_FIELDS = {
    'object_id', 'label', 'segmentation_prompt', 'count', 'representation',
    'requires_articulation', 'seed',
}
_LAYOUT_CAMERA_FIELDS = {
    'position', 'target', 'up', 'focal_length_mm', 'horizontal_aperture_mm',
}


def _validate_layout_camera(camera):
    if not isinstance(camera, dict) or set(camera) - _LAYOUT_CAMERA_FIELDS:
        raise ValueError('layout.camera accepts position, target, up, focal_length_mm and horizontal_aperture_mm')

    vectors = {}
    for field in ('position', 'target', 'up'):
        if field not in camera:
            continue
        value = camera[field]
        if (not isinstance(value, list) or len(value) != 3 or
                any(type(component) not in (int, float) or not math.isfinite(component)
                    for component in value)):
            raise ValueError(f'layout.camera.{field} must contain three finite numbers')
        vectors[field] = value
    if 'position' not in vectors or 'target' not in vectors:
        raise ValueError('layout.camera requires position and target')
    direction = [target - origin for origin, target in zip(vectors['position'], vectors['target'])]
    if sum(component * component for component in direction) < 1e-16:
        raise ValueError('layout.camera position and target must differ')

    focal = camera.get('focal_length_mm', 24)
    aperture = camera.get('horizontal_aperture_mm', 36)
    for field, value in (('focal_length_mm', focal), ('horizontal_aperture_mm', aperture)):
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise ValueError(f'layout.camera.{field} must be positive and finite')

    if 'up' in vectors:
        up = vectors['up']
        cross = [direction[1] * up[2] - direction[2] * up[1],
                 direction[2] * up[0] - direction[0] * up[2],
                 direction[0] * up[1] - direction[1] * up[0]]
        if sum(component * component for component in cross) < 1e-16:
            raise ValueError('layout.camera up must not be parallel to its viewing direction')


# 根据场景意图构建默认设计简报，优先采用显式提供的布局参考。
def default_layout(intent):
    """New scenes begin with a visible design brief; explicit references take priority."""
    return {'mode':'generate','prompt':'Real interior architectural photograph, slightly elevated wide three-quarter perspective showing the complete room layout, major objects, useful aisles and credible support surfaces. Natural window illumination with soft indirect light, contact shadows, restrained highlights retaining texture. Distinct ceramic, metal, wood and fabric roughness; subtle wear and material grain, realistic scale, recognizable object shapes. Show the functional character of a room in real use. Photographic realism, not a floor plan, sketch, watercolor, text-labelled diagram, toy scene or plastic CGI. Scene brief: '+intent['description'][:5000]}


# 规范图像布局中的对象清单，补齐可复现的分割提示和实例数量。
def _validate_layout_objects(objects):
    if not isinstance(objects, list) or not 1 <= len(objects) <= 128:
        raise ValueError('layout.objects must contain 1..128 object descriptions')
    identities = set()
    total_instances = 0
    normalized = []
    for item in objects:
        if not isinstance(item, dict) or set(item) - _LAYOUT_OBJECT_FIELDS:
            raise ValueError('layout objects accept object_id, label, segmentation_prompt, count, representation, requires_articulation and seed')
        object_id = item.get('object_id')
        label = item.get('label')
        if not isinstance(object_id, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,63}', object_id):
            raise ValueError('layout object_id must be a SceneProgram-compatible identifier')
        if not isinstance(label, str) or not 1 <= len(label.strip()) <= 200:
            raise ValueError('layout object label must contain 1..200 characters')
        prompt = item.get('segmentation_prompt', label)
        if not isinstance(prompt, str) or not 1 <= len(prompt.strip()) <= 300:
            raise ValueError('segmentation_prompt must contain 1..300 characters')
        count = item.get('count', 1)
        if type(count) is not int or not 1 <= count <= 32:
            raise ValueError('layout object count must be 1..32')
        representation = item.get('representation', 'auto')
        if representation not in {'auto', 'trellis', 'native'}:
            raise ValueError('layout representation must be auto, trellis or native')
        requires_articulation = item.get('requires_articulation', False)
        if type(requires_articulation) is not bool:
            raise ValueError('requires_articulation must be a boolean')
        seed = item.get('seed')
        if seed is not None and (type(seed) is not int or not 0 <= seed < 2**32):
            raise ValueError('layout object seed must be an integer from 0 to 2^32-1')
        expanded = ([object_id] if count == 1 else
                    [f'{object_id}_{index}' for index in range(1, count + 1)])
        if identities.intersection(expanded):
            raise ValueError('layout object IDs collide after count expansion')
        identities.update(expanded)
        total_instances += count
        normalized.append({**item, 'object_id': object_id, 'label': label.strip(),
                           'segmentation_prompt': prompt.strip(), 'count': count,
                           'representation': representation,
                           'requires_articulation': requires_articulation})
    if total_instances > 256:
        raise ValueError('layout object inventory may describe at most 256 instances')
    return normalized


# 检查布局设计声明的类型、参考方式及必要字段。
def validate_layout(spec):
    if not isinstance(spec,dict) or set(spec)-{'mode','prompt','source_image','source_view','seed','design_task_id','objects','camera'}:
        raise ValueError('invalid layout guidance fields')
    mode=spec.get('mode')
    if 'design_task_id' in spec:
        if mode!='reference' or set(spec)-{'mode','design_task_id','objects','camera'} or not isinstance(spec['design_task_id'],str) or not re.fullmatch(r'sf_[a-f0-9]{16}\.scene\d+',spec['design_task_id']):
            raise ValueError('design_task_id requires mode=reference and a completed layout task ID; optional objects can request a fresh estimate')
    if mode not in {'generate','edit','reference'}:raise ValueError('layout mode must be generate, edit or reference')
    if mode!='reference' and (not isinstance(spec.get('prompt'),str) or not 1<=len(spec['prompt'])<=6000):
        raise ValueError('generated or edited layout needs a visual prompt')
    if 'source_image' in spec and (not isinstance(spec['source_image'],str) or not spec['source_image']):
        raise ValueError('layout source_image must be a registered file path')
    if 'source_view' in spec and (type(spec['source_view']) is not int or not 0<=spec['source_view']<=11):
        raise ValueError('layout source_view must be a captured view index 0..11')
    if 'source_image' in spec and 'source_view' in spec:raise ValueError('choose one layout image source')
    if mode=='generate' and ('source_image' in spec or 'source_view' in spec):raise ValueError('generate mode has no source image; use edit')
    if type(spec.get('seed',42)) is not int or not 0<=spec.get('seed',42)<2**32:raise ValueError('invalid layout seed')
    if 'objects' in spec: _validate_layout_objects(spec['objects'])
    if 'camera' in spec:
        _validate_layout_camera(spec['camera'])
    return spec


# 生成或复用布局参考图，并把设计依据和状态写入本次工作目录。
def prepare_layout(tools,intent,directory,stop,parent_directory=None,design_directory=None):
    from capabilities.asset_generation.generation import _file_record, generation_defaults

    spec=intent.get('layout')
    inherited=None
    prepared=None
    if spec and spec.get('design_task_id'):
        validate_layout(spec)
        if design_directory is None:raise ValueError('design_task_id must be resolved to a completed layout task')
        original=json.loads((design_directory/'layout_reference.json').read_text())
        if original.get('role')!='design_reference_only' or original.get('gt_source') is not False:
            raise ValueError('prepared reference lacks design-only provenance')
        prepared={'design_task_id':spec['design_task_id'],'reference_receipt':original}
    if spec is None:
        if parent_directory is None:return None
        inherited_image=parent_directory/'layout_reference.png'
        inherited_receipt=parent_directory/'layout_reference.json'
        if not inherited_image.exists() and not inherited_receipt.exists():return None
        if not inherited_image.is_file() or not inherited_receipt.is_file():raise ValueError('parent design reference is incomplete; supply an explicit layout source')
        original=json.loads(inherited_receipt.read_text())
        if original.get('role')!='design_reference_only' or original.get('gt_source') is not False:raise ValueError('parent reference lacks design-only provenance')
        spec={'mode':'reference','source_image':str(inherited_image)}
        inherited={'parent_revision_path':str(parent_directory),'reference_receipt':original}
    validate_layout(spec)
    record_progress(directory,'layout_reference',mode=spec['mode'])
    # A task's quality/engineering retries share one frozen design reference.
    work=directory.parent/'layout';work.mkdir(parents=True,exist_ok=True)
    source=None
    if prepared:source=design_directory/'layout_reference.png'
    elif inherited:source=inherited_image  # Parent path was resolved by the trusted scene lineage.
    elif spec.get('source_image'):source=tools.source_path(spec['source_image'])
    elif spec['mode'] in {'edit','reference'}:
        if parent_directory is None:raise ValueError('layout edit/reference needs source_image or a captured parent scene')
        source=parent_directory/'capture'/f'view_{spec.get("source_view",0)}.png'
        if not source.is_file():raise ValueError('requested layout source view is unavailable')
    contract={'spec':spec,'source':_file_record(source) if source else None}
    contract_file=work/'contract.json'
    if contract_file.exists() and json.loads(contract_file.read_text())!=contract:
        raise ValueError('layout source or request changed; create a new scene task')
    write_json(contract_file,contract)
    image=work/'reference.png';receipt=work/'receipt.json'
    if not receipt.exists():
        if stop.is_set():raise RuntimeError('layout generation canceled')
        if spec['mode']=='reference':
            with Image.open(source) as original:original.convert('RGB').save(image)
            tool='prepared_design_reference' if prepared else 'inherited_design_reference' if inherited else 'source_image'
        else:
            prompt=spec['prompt']+'\nCreate a coherent spatial design reference, with readable room layout and furniture relationships. No text labels or invented numeric measurements. This image is a design proposal, not ground truth or a validated simulation.'
            if spec['mode']=='edit':prompt+=' Preserve relevant existing structure and implement the requested layout changes in the supplied view.'
            defaults = generation_defaults('diffusion')
            request = {**defaults, 'prompt': prompt, 'output': str(image), 'seed': spec.get('seed', defaults['seed'])}
            if source:request['image']=str(source)
            generated=tools.execute('diffusion',request,work/'diffusion',stop)
            tool=generated['model']
        with Image.open(image) as result:size=list(result.size)
        write_json(receipt,{'schema':'benchforge_backend.layout-reference/v1','mode':spec['mode'],'tool':tool,'image':'layout_reference.png',
                           'source':contract['source'],'size':size,'seed':spec.get('seed',42),
                           'role':'design_reference_only','metric_layout_verified':False,'gt_source':False,
                           **({'inherited_from':inherited} if inherited else {}),
                           **({'prepared_from':prepared} if prepared else {}),
                           'limitations':['Image edits do not prove preservation of existing 3D geometry.',
                                          'SceneProgram and desktop Isaac determine actual dimensions, collisions and GT.',
                                          'Unseen geometry and objects may differ from the design image.']})
    if not image.is_file() or not image.stat().st_size:raise ValueError('layout receipt has no image')
    result=json.loads(receipt.read_text())
    estimate = None
    source_estimate = design_directory/'layout_estimate.json' if prepared else None
    source_contract_path = (design_directory.parent/'layout'/'reference_layout'/'contract.json'
                            if prepared else None)
    can_reuse_estimate = bool(source_estimate and source_estimate.is_file() and not spec.get('objects'))
    if spec.get('objects') and source_estimate and source_estimate.is_file() and source_contract_path.is_file():
        source_contract = json.loads(source_contract_path.read_text(encoding='utf-8'))
        can_reuse_estimate = (source_contract.get('objects') == spec['objects'] and
                              source_contract.get('camera') == spec.get('camera') and
                              source_contract.get('seed', 42) == spec.get('seed', 42))
    if can_reuse_estimate:
        estimate = json.loads(source_estimate.read_text(encoding='utf-8'))
    elif inherited and (parent_directory/'layout_estimate.json').is_file() and not spec.get('objects'):
        estimate = json.loads((parent_directory/'layout_estimate.json').read_text(encoding='utf-8'))
        overlay = parent_directory/'layout_estimate_overlay.png'
        if overlay.is_file() and not overlay.is_symlink():
            shutil.copy2(overlay, work/'layout_estimate_overlay.png')
    elif spec.get('objects'):
        from capabilities.asset_generation.reference_layout_stage import prepare_reference_layout
        estimate = prepare_reference_layout(tools, image, spec, work, stop)
    if estimate is not None:
        estimate_path=directory/'layout_estimate.json'
        write_json(estimate_path,estimate)
        overlay=Path(estimate.get('overlay_path',''))
        if overlay.is_file() and not overlay.is_symlink():
            shutil.copy2(overlay,directory/'layout_estimate_overlay.png')
        result['layout_estimate']={'file':'layout_estimate.json','status':estimate['status'],
                                   'objects':len(estimate['objects']), 'gt_source':False}
        write_json(receipt,result)
    shutil.copy2(image,directory/'layout_reference.png')
    write_json(directory/'layout_reference.json',result)
    return directory/'layout_reference.png'


# 从设计记录解析并验证对应的设计任务目录。
def design_directory(store, layout):
    task_id = (layout or {}).get('design_task_id')
    if not task_id:
        return None
    validate_layout(layout)
    snapshot = store.snapshot(task_id.split('.')[0])
    task = next((t for t in snapshot['tasks'] if t['id'] == task_id), None)
    if not task or task['unit'].get('workflow') != 'layout':
        raise ValueError('design_task_id must identify a benchforge_layout task')
    if snapshot['state'] != 'SUCCEEDED' or task['state'] != 'SUCCEEDED':
        raise ValueError('design reference is not complete; use status/wait on its existing run')
    directory = store.root / task_id / f"revision_{task['unit']['revision']}"
    if not (directory / 'layout_reference.png').is_file() or not (directory / 'layout_reference.json').is_file():
        raise ValueError('completed design has no reference image/receipt')
    return directory


# 校验显式布局参考及其来源；省略参考时交由后续流程生成。
def require_prior_reference(store, intent, parent_directory=None):
    """Validate an explicit reference; the pipeline generates omitted references."""
    layout = intent.get('layout')
    design = design_directory(store, layout)
    if intent.get('scene_program_path'):
        return
    if layout and layout.get('objects'):
        return
    if design and (design / 'layout_estimate.json').is_file():
        return
    if parent_directory and (parent_directory / 'layout_estimate.json').is_file():
        return
    raise ValueError(
        'Image-led scene planning needs layout.objects, or a completed benchforge_layout task '
        'that contains layout_estimate.json. The estimate is the starting placement for SceneProgram.'
    )


# 验证设计请求并创建可恢复执行的布局设计任务。
def start_design_request(store, value):
    if not isinstance(value, dict) or set(value) - {'request_key', 'name', 'description', 'layout', 'context'}:
        raise ValueError('layout request fields: request_key, name, description, optional layout')
    for key, limit in (('request_key', 200), ('name', 200), ('description', 12000)):
        if not isinstance(value.get(key), str) or not 1 <= len(value[key]) <= limit:
            raise ValueError('invalid layout ' + key)
    layout = validate_layout(value.get('layout') or default_layout(value))
    if 'source_view' in layout or (
        layout['mode'] != 'generate'
        and not layout.get('source_image')
        and not layout.get('design_task_id')
    ):
        raise ValueError('standalone edit/reference needs source_image; copy a prior PNG with evidence and workspace_id')
    design = design_directory(store, layout) if layout.get('design_task_id') else None
    if not layout.get('objects') and not (
        design and (design / 'layout_estimate.json').is_file()
    ):
        raise ValueError(
            'benchforge_layout needs layout.objects to run FastSAM3, FastDA3 and FastTrellis, '
            'or a completed design_task_id that already has layout_estimate.json'
        )
    intent = {'name': value['name'], 'description': value['description'], 'layout': layout}
    # 宿主附加的运行身份单独交给存储层，不能混入布局意图或模型参数。
    context = RunContext.from_request(value.get('context'))
    kwargs = {'context': context} if context is not None else {}
    return {'run_id': store.submit(value['request_key'], [intent], 'layout', **kwargs), 'state': 'accepted',
            'workflow': 'layout', 'next_action': 'wait_then_inspect_design', 'gt_source': False}


# 执行布局设计任务，生成参考材料并保存可供后续场景使用的回执。
def execute_design(store, task, stop):
    from capabilities.asset_generation.generation import GenerationTools

    directory = store.directory(task['id'], task['unit']['revision'])
    prepare_layout(GenerationTools(store.root, engine=store.repo.engine, task_id=task['id']), task['unit']['intent'], directory, stop)
    receipt = json.loads((directory / 'layout_reference.json').read_text())
    result = {'role': 'design_reference_only', 'gt_source': False, 'simulator_capture': False,
              'reference': {'tool': 'benchforge_scene_evidence', 'task_id': task['id'], 'file': 'layout/reference.png'},
              'scene_layout': {'mode': 'reference', 'design_task_id': task['id']}, 'receipt': receipt,
              'next_action': ('inspect_reference_and_layout_estimate_then_write_scene_program'
                              if receipt.get('layout_estimate') else 'inspect_reference_then_write_scene_program')}
    if receipt.get('layout_estimate'):
        result['layout_estimate']={'tool':'benchforge_scene_evidence','task_id':task['id'],
                                   'file':'layout_estimate.json','summary':receipt['layout_estimate']}
    record_progress(directory, 'complete', role='design_reference_only')
    store.update(task['id'], state='SUCCEEDED', result=result, unit={**task['unit'], 'phase': 'complete'})
