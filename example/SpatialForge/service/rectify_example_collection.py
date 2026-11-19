"""Rebuild this example's data and records with the current production API."""
from copy import deepcopy
from collections import Counter
from datetime import datetime, timezone
import argparse
import json
import math
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rectify_example_records as r

CONFIG = str(r.ROOT / 'config/generated/benchforge-server.local.json')
NEW_RUN = r.BENCH / 'work/production-rectified-20261116'
CONTROL = r.AUDIT / 'production-control.json'


# 返回当前UTC时间的ISO格式字符串，供迁移与操作审计记录使用。
def now():
    return datetime.now(timezone.utc).isoformat()


# 首次改写前将既有文件复制到审计备份目录，已有备份保持不变。
def keep(path):
    path = Path(path)
    if path.is_file():
        target = r.AUDIT / 'collection-before' / path.relative_to(r.ROOT)
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)


# 先保留原文件备份，再通过示例公共写入函数保存新内容。
def save(path, value):
    keep(path)
    r.write(path, value)


# 使用SQLite备份接口冻结运行与阶段审查索引，避免直接复制活动数据库。
def snapshot_indexes():
    for name in ('runs.sqlite', 'stage-reviews.sqlite'):
        source = r.ROOT / name
        destination = r.AUDIT / 'collection-before' / name
        if source.is_file() and not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(source) as original, sqlite3.connect(destination) as backup:
                original.backup(backup)


# 从保留的原生场景、表面和相机记录重算各模板答案，并核对旧答案后生成显式测量依据。
def native_answers(records):
    """Produce annotations from the transported native scene, not copied GT."""
    import importlib.util
    if importlib.util.find_spec('pxr') is None:
        sys.path.append('/local_data/jiangbaoyang/isaac-a800-20261016/venv/lib/python3.12/site-packages')
    import numpy as np
    from public_scale import camera_axes
    from surface_oracle import NativeSurfaceOracle
    extractor = r.load_extractor()
    context = {}
    for scene, task_id in r.SCENES.items():
        folder = r.EXAMPLE / 'runtime' / task_id / 'revision_0'
        evidence = r.read(folder / 'capture/evidence.json')
        objects = [json.loads(line) for line in (r.BENCH / 'annotations' / scene / 'objects.jsonl').read_text().splitlines() if line.strip()]
        props = [obj for obj in objects if not obj['structural']]
        for index, obj in enumerate(props, 1):
            obj['tag'] = f'{"ABC"[list(r.SCENES).index(scene)]}{index:02d}'
        bodies = [(obj['object_id'], evidence['entities'][obj['object_id']]['prim_path']) for obj in props]
        sizes, _ = extractor.frozen_body_sizes(str(folder / 'capture/scene.usdc'), bodies)
        for obj in props:
            assert np.allclose(obj['settled_center_m'], evidence['entities'][obj['object_id']]['settled_center'], atol=1e-8)
            assert np.allclose(obj['size_xyz_m'], sizes[obj['object_id']]['size_xyz_m'], atol=1e-7)
        context[scene] = {'folder': folder, 'evidence': evidence,
                          'objects': {obj['object_id']: obj for obj in props},
                          'cameras': {row['frame_id']: row for row in evidence['frames']},
                          'oracle': NativeSurfaceOracle(folder), 'items': {}}
    output = {}
    for row in records:
        original = r.read(row['provenance']['path'])
        private = original['private']
        ctx = context[private['scene']]
        objects = [ctx['objects'][key] for key in private['objects']]
        template = row['template_id']
        center = lambda obj: obj['settled_center_m']
        basis = 'native settled object centers and explicitly declared measurement rules'
        if template == 'C01-T01':
            value = round(math.dist(center(objects[0]), center(objects[1])), 2)
        elif template == 'C01-T02':
            measured = ctx['oracle'].measure_pair(objects[0]['object_id'], objects[1]['object_id'],
                                                  [ctx['cameras'][key] for key in private['oracle_views']])
            if not measured:
                raise ValueError('No native triangle-surface witness for ' + row['id'])
            value = measured[0]['answer_m']
            basis = 'native triangle surfaces and exact BVH closest-point measurement'
        elif template == 'C01-T03':
            value = round(math.dist(center(objects[0]), private['point_world']), 2)
        elif template == 'C03-T01':
            value = round(math.dist(center(objects[0]), ctx['cameras'][private['oracle_views'][0]]['camera']['position']), 2)
        elif template in ('C04-T01', 'C04-T02'):
            ordered = sorted(objects[1:], key=lambda obj: math.dist(center(objects[0]), center(obj)))
            value = ordered[0]['tag'] if template.endswith('T01') else [obj['tag'] for obj in ordered]
        elif template == 'C05-T01':
            axes, origin = camera_axes(ctx['cameras'][private['oracle_views'][0]]['camera'])
            depths = [-sum(axes[2][i] * (center(obj)[i] - origin[i]) for i in range(3)) for obj in objects]
            value = '相同' if abs(depths[0] - depths[1]) <= row['parameters']['epsilon'] else '更小' if depths[0] < depths[1] else '更大'
        elif template == 'C07-T02':
            value = round(max(objects[0]['size_xyz_m']) * 100, 1)
            basis = 'frozen native USD body-local geometry bounds, in centimetres'
        elif template == 'C09-T01':
            value = max(objects, key=lambda obj: max(obj['size_xyz_m']))['tag']
            basis = 'frozen native USD body-local geometry bounds'
        elif template == 'C11-T01':
            value = sum(math.dist(center(objects[0]), center(obj)) <= row['parameters']['radius'] for obj in objects[1:])
        else:
            raise ValueError('Unsupported example template: ' + template)
        assert value == original['answer'], 'Native measurement differs: ' + row['id']
        if template in ('C01-T01', 'C01-T02', 'C01-T03', 'C03-T01', 'C07-T02'):
            answer = {'value': value, 'unit': original['unit']}
        elif template in ('C04-T01', 'C09-T01'):
            answer = {'choice_id': value}
        elif template == 'C04-T02':
            answer = {'sequence': value}
        elif template == 'C05-T01':
            answer = {'label': value}
        else:
            answer = {'value': value}
        authority = r.read(r.BENCH / 'items/authority' / (row['id'] + '.json'))
        assert authority['public_reconstruction_validation']['verified'] is True
        output[row['id']] = {'answer': answer, 'original': original, 'objects': objects, 'context': ctx,
                             'measurement': {'basis': basis, 'objects': private['objects'],
                                             'views': private['oracle_views'],
                                             'native_scene': r.file_ref(ctx['folder'] / 'capture/scene.usdc'),
                                             'matches_retained_answer': True}}
        if len(output) % 10 == 0:
            print('native annotations rebuilt', len(output), '/100', flush=True)
    return output, context


# 依据重算结果更新题干、原始观察与标注附件说明及答案输出合同。
def question(row, measured):
    original = measured['original']
    params = deepcopy(row['parameters'])
    for key, value in params.items():
        if isinstance(value, str):
            params[key] = value.replace('含对象编号框与标记点', '编号与标记点见同机位附件：').replace('含对象编号框', '对象编号见同机位附件')
    template = row['template_id']
    spec = deepcopy(row['question_spec'])
    prefix = '观察原始采集图及对应标注附件，'
    stems = {
        'C01-T01': lambda: f"{prefix}估计{params['A']}与{params['B']}几何中心之间的直线距离。",
        'C01-T02': lambda: f"{prefix}估计{params['A']}与{params['B']}表面之间的最短距离。",
        'C01-T03': lambda: f"{prefix}估计{params['A']}几何中心到标记点{params['P']}的直线距离。",
        'C03-T01': lambda: f"{prefix}估计图1对应相机光心到{params['object']}几何中心的直线距离。",
        'C04-T01': lambda: f"{prefix}从公开候选中选出几何中心距离{params['anchor']}最近的对象。",
        'C04-T02': lambda: f"{prefix}按几何中心距离{params['anchor']}由近到远排列公开候选对象。",
        'C05-T01': lambda: f"{prefix}比较{params['A']}与{params['B']}在图1相机坐标系中的光轴深度。",
        'C07-T02': lambda: f"{prefix}估计{params['object']}本体局部包围盒的最长边。",
        'C09-T01': lambda: f"{prefix}在{params['A']}与{params['B']}中，选出本体局部包围盒最长边更长的对象。",
        'C11-T01': lambda: f"{prefix}统计公开候选中，几何中心距离{params['anchor']}不超过{params['radius']}米的对象数。",
    }
    spec['schema'] = 'benchforge.question-writing/v1'
    spec['stem'] = stems[template]()
    spec['context'] = [text.replace('公开相机标定卡见图片', '公开相机标定卡见附件') for text in spec['context']]
    spec['context'].insert(0, '图1起为未加标记的原始采集图；同机位带框图片、编号、黄十字标记点和相机尺度卡均在公开附件中，按机位名称一一对应。')
    spec['media_labels'] = [f'图{index + 1}：{frame} 原始RGB观测（对象编号与点标记见同机位标注附件）'
                            for index, frame in enumerate(original['private']['views'])]
    spec['parameter_bindings'] = params
    answer = measured['answer']
    if template in ('C04-T01', 'C09-T01'):
        candidates = measured['objects'][1:] if template == 'C04-T01' else measured['objects']
        spec['options'] = [obj['tag'] + '：' + obj['label'] for obj in candidates]
    if 'unit' in answer:
        spec['response'] = f"提交 JSON 对象，value 为数值，unit 固定为 {answer['unit']}。"
    elif 'choice_id' in answer:
        spec['response'] = '提交 JSON 对象，choice_id 为所选对象的公开编号。'
    elif 'sequence' in answer:
        spec['response'] = '提交 JSON 对象，sequence 为按距离由近到远排列的公开编号数组。'
    elif 'label' in answer:
        spec['response'] = '提交 JSON 对象，label 为更小、更大或相同，表示第一个对象相对第二个对象的光轴深度。'
    else:
        spec['response'] = '提交 JSON 对象，value 为符合条件的非负整数对象数。'
    return params, spec


# 备份旧材料后重建示例标注和证据绑定，重新执行场景验收并记录迁移完成标记。
def migrate():
    _, store = r.setup()
    marker = r.AUDIT / 'collection-assets-rebuilt.json'
    if marker.exists():
        print(json.dumps(r.read(marker), ensure_ascii=False), flush=True)
        return
    snapshot_indexes()
    evidence_path = r.BENCH / 'evidence/evidence.calibrated-20261106.jsonl'
    records = [json.loads(line) for line in evidence_path.read_text().splitlines() if line.strip()]
    assert len(records) == 100
    measured, contexts = native_answers(records)
    from capabilities.task_catalog.benchmark import all_templates
    from capabilities.dataset_authoring.answer_contracts import validate_answer
    from capabilities.dataset_authoring.question_writing import validate_spec
    from capabilities.dataset_authoring.quality import validate_choice
    templates = {template['id']: template for template in all_templates()}
    for row in records:
        item = measured[row['id']]
        ctx = item['context']
        source = Path(row['provenance']['path'])
        params, spec = question(row, item)
        original_media = list(row['media'])
        row['media'] = [str(ctx['folder'] / 'capture' / (frame + '.png')) for frame in item['original']['private']['views']]
        row['assets'] = list(dict.fromkeys([*row.get('assets', []), *original_media]))
        row['parameters'], row['question_spec'] = params, spec
        row['provenance'].update(kind='simulation', path=str(ctx['folder'] / 'capture/evidence.json'),
                                 generator=str(Path(__file__).resolve()),
                                 annotation_source=str(source),
                                 annotation_phase='derived_after_native_capture', capture_repeated=False)
        row['answer_selector'] = '/collection_annotations/items/' + row['id'] + '/answer'
        row['admission_selector'] = '/collection_annotations/items/' + row['id'] + '/admission'
        row['selectors'] = {'answer': row['answer_selector'], 'admission': row['admission_selector']}
        validate_answer(templates[row['template_id']], item['answer'])
        presentation = validate_spec(spec, params, len(row['media']))
        validate_choice(templates[row['template_id']], item['answer'], presentation)
        entry = {'template_id': row['template_id'], 'answer': item['answer'],
                 'admission': deepcopy(item['original']['admission']),
                 'measurement': item['measurement'], 'annotation_source': str(source),
                 'raw_observations': row['media'], 'public_annotations': original_media,
                 'created_at': now(), 'capture_repeated': False}
        ctx['items'][row['id']] = entry
        updated_source = {**item['original'], 'answer': item['answer'], 'parameters': params,
                          'question_spec': spec, 'collection_source': row['provenance']['path'],
                          'answer_selector': row['answer_selector'], 'annotation_measurement': item['measurement']}
        save(source, updated_source)
        authority_path = r.BENCH / 'items/authority' / (row['id'] + '.json')
        authority = r.read(authority_path)
        authority.update(answer=item['answer'], source_version=r.file_ref(source),
                         media_versions=[r.file_ref(name) for name in row['media']],
                         asset_versions=[r.file_ref(name) for name in row['assets']],
                         native_measurement=item['measurement'])
        save(authority_path, authority)
    from capabilities.scene_authoring.validation.acceptance import capture_acceptance
    from storage.scene_jobs.metadata import tasks
    from sqlalchemy import select
    accepted = []
    for scene, ctx in contexts.items():
        folder = ctx['folder']
        native_path = folder / 'capture/evidence.json'
        original_version = r.file_ref(native_path)
        ctx['evidence']['collection_annotations'] = {
            'schema': 'benchforge.scene-annotations/v1', 'phase': 'derived_after_native_capture',
            'created_at': now(), 'capture_repeated': False, 'original_evidence_version': original_version,
            'method': str(Path(__file__).resolve()), 'items': ctx['items']}
        save(native_path, ctx['evidence'])
        review = r.read(folder / 'scene_review.json')
        receipt = capture_acceptance(store.root, r.read(folder / 'program.json'), r.read(folder / 'capture/report.json'),
                                     ctx['evidence'], review, folder / 'capture', execution_mode='production')
        if not receipt['passed']:
            raise ValueError('Rebuilt native annotation container did not pass scene acceptance: ' + str(receipt['issues']))
        save(folder / 'scene_acceptance.json', receipt)
        review['production_acceptance'] = receipt
        save(folder / 'scene_review.json', review)
        task_id = r.SCENES[scene]
        with store.repo.transaction() as connection:
            task = dict(connection.execute(select(tasks).where(tasks.c.id == task_id)).mappings().one())
            result = deepcopy(task['result'])
            result['scene_review'] = review
            connection.execute(tasks.update().where(tasks.c.id == task_id).values(result=result))
            store.repo.event(connection, task['run_id'], task_id, 'benchforge.example_native_annotations_rebuilt',
                             {'items': len(ctx['items']), 'capture_repeated': False,
                              'method': str(Path(__file__).resolve()), 'original_evidence_version': original_version})
        accepted.append({'task_id': task_id, 'revision': 0, 'annotations': len(ctx['items']), 'passed': receipt['passed']})
    keep(evidence_path)
    evidence_path.write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in records))
    save(marker, {'schema': 'benchforge.example-collection-rectification/v1',
                  'created_at': now(), 'items': len(records), 'scenes': accepted,
                  'per_template': dict(Counter(row['template_id'] for row in records)),
                  'original_media_unchanged': True, 'capture_repeated': False,
                  'media_contract': 'raw accepted RGB observations; annotated images and calibration cards are explicit public assets',
                  'annotation_contract': 'native measurements stored as explicit post-capture annotations in the accepted evidence record',
                  'evidence': str(evidence_path), 'production_receipt_pending': True})
    print(json.dumps(r.read(marker), ensure_ascii=False), flush=True)


# 为一次本地操作记录请求与数据库状态，保存结果或失败原因后返回操作身份。
def recorded(tool, arguments, handler):
    from storage.operation_index import ensure_schema
    operation = uuid.uuid4().hex[:16]
    directory = r.EXAMPLE / 'operations' / operation
    directory.mkdir(parents=True)
    save(directory / 'request.json', {'tool': tool, 'arguments': arguments, 'time': now(),
                                     'context': {'session_id': r.SESSION, 'call_id': 'example-rectification-20261116'}})
    with sqlite3.connect(r.ROOT / 'runs.sqlite', timeout=30) as db:
        ensure_schema(db, r.ROOT)
        db.execute('INSERT INTO operations VALUES (?,?,?,?,?,?)',
                   (operation, tool, 'running', str(directory), None, r.SESSION))
    try:
        result = handler(directory)
        save(directory / 'result.json', result)
        with sqlite3.connect(r.ROOT / 'runs.sqlite', timeout=30) as db:
            db.execute("UPDATE operations SET state='completed' WHERE id=?", (operation,))
        return {'operation_id': operation, 'artifact_dir': str(directory), **result}
    except Exception as error:
        with sqlite3.connect(r.ROOT / 'runs.sqlite', timeout=30) as db:
            db.execute("UPDATE operations SET state='failed',error=? WHERE id=?", (str(error), operation))
        raise


# 按冻结证据恢复场景原定分区，只修正导入记录而不重新划分既有样本。
def repair_partitions():
    """Correct imported default scene partitions to the frozen dataset split."""
    _, store = r.setup()
    from sqlalchemy import select
    from storage.scene_jobs.metadata import runs, tasks
    rows = [json.loads(line) for line in (r.BENCH / 'evidence/evidence.calibrated-20261106.jsonl').read_text().splitlines() if line.strip()]
    changes = []
    for task_id in r.SCENES.values():
        partitions = {row['split'] for row in rows if row['provenance']['task_id'] == task_id}
        assert len(partitions) == 1
        split = partitions.pop()
        with store.repo.transaction() as connection:
            task = dict(connection.execute(select(tasks).where(tasks.c.id == task_id)).mappings().one())
            saved = dict(connection.execute(select(runs).where(runs.c.id == task['run_id'])).mappings().one())
            unit = deepcopy(task['unit'])
            previous = unit['intent'].get('split')
            if previous == split:
                continue
            unit['intent']['split'] = split
            plan = deepcopy(saved['plan'])
            for intent in plan.get('intents', []):
                intent['split'] = split
            connection.execute(tasks.update().where(tasks.c.id == task_id).values(unit=unit))
            connection.execute(runs.update().where(runs.c.id == task['run_id']).values(plan=plan))
            change = {'task_id': task_id, 'previous_imported_default': previous, 'split': split,
                      'basis': 'frozen source evidence and original per-scene dataset allocation',
                      'existing_items_repartitioned': False}
            store.repo.event(connection, task['run_id'], task_id, 'benchforge.example_partition_record_corrected', change)
            changes.append(change)
    path = r.AUDIT / 'partition-record-correction.json'
    if changes or not path.exists():
        save(path, {'schema': 'benchforge.example-partition-record-correction/v1', 'created_at': now(), 'changes': changes})
    print(json.dumps(r.read(path), ensure_ascii=False), flush=True)


# 复用已保存控制记录，或用已验收场景生成生产计划并启动首阶段审查。
def start():
    r.setup()
    if CONTROL.exists():
        print(json.dumps(r.read(CONTROL), ensure_ascii=False), flush=True)
        return
    assert (r.AUDIT / 'collection-assets-rebuilt.json').is_file()
    old = r.read(r.RUN / 'plan.json')
    from service_access.production_receipts import SceneReceipts
    receipts = SceneReceipts(CONFIG).require([{'task_id': task_id, 'revision': 0} for task_id in r.SCENES.values()])
    assessment = deepcopy(old['assessment'])
    assessment['scenes']['designs'] = [row['scene_design'] for row in receipts]
    inputs = deepcopy(old['inputs'])
    inputs.update(service_config=CONFIG, scene_sources=[{'task_id': task_id, 'revision': 0} for task_id in r.SCENES.values()])
    arguments = {'pipeline_id': 'data.capability', 'objective': old['objective'],
                 'assessment': assessment, 'inputs': inputs, 'selections': old['selections']}
    from pipelines.benchmark.design.planner import plan
    from pipelines.benchmark.design.plan_versions import activate
    from pipelines.reviews.service import gate

    # 通过生产规划器生成计划，绑定当前工作区，并登记首阶段审查。
    def create(directory):
        # Draft through the production planner, then bind to the live repository
        # workspace; the artifacts themselves intentionally stay under example/.
        result = plan(arguments, directory)
        activate(r.ROOT, r.SESSION, result['plan'])
        saved = r.read(result['plan'])
        result['stage_review'] = gate(r.ROOT, r.SESSION, 'S1', saved['stage_review_key'], result['plan'])
        return result

    result = recorded('pipeline_plan', arguments, create)
    save(CONTROL, {'plan': result['plan'], 'run': str(NEW_RUN), 'session': r.SESSION,
                   'workspace': str(r.ROOT), 'plan_operation': result['operation_id'], 'created_at': now()})
    print(json.dumps({'control': r.read(CONTROL), 'stage_review': result.get('stage_review')}, ensure_ascii=False), flush=True)


# 读取阶段审查门禁；允许继续时运行下一段生产流程并保存紧凑进度。
def advance():
    r.setup()
    control = r.read(CONTROL)
    from pipelines.reviews.service import review
    status = review(r.ROOT, r.SESSION)
    if status.get('state') in ('paused', 'needs_input', 'check_failed'):
        result = {'state': status['state'], 'stage_review': status}
        print(json.dumps(result, ensure_ascii=False), flush=True)
        return result
    if not status.get('can_continue'):
        result = {'state': 'waiting_user', 'stage_review': status}
        print(json.dumps({'state': result['state'], 'stage_review': {key: status.get(key) for key in
              ('stage', 'review_id', 'round_number', 'waits_completed', 'remaining_seconds', 'deadline')}}, ensure_ascii=False), flush=True)
        return result
    from pipelines.benchmark.execution.runner import run
    arguments = {'plan': control['plan'], 'run': control['run']}

    # 为生产运行补齐工作区与会话身份，并调用统一流程执行器。
    def execute(directory):
        return run({**arguments, '_workspace': str(r.ROOT), '_dsh_context': {'session_id': r.SESSION}}, directory)

    result = recorded('pipeline_run', arguments, execute)
    compact = {key: result[key] for key in ('operation_id', 'run', 'state', 'error', 'next', 'stage_review') if key in result}
    compact['steps'] = {key: {'state': value['state'], 'items': (value.get('result') or {}).get('items')}
                        for key, value in result.get('steps', {}).items()}
    save(r.AUDIT / 'production-latest.json', compact)
    display = deepcopy(compact)
    if display.get('stage_review'):
        display['stage_review'] = {key: compact['stage_review'].get(key) for key in
                                   ('stage', 'state', 'review_id', 'round_number', 'waits_completed', 'remaining_seconds')}
    print(json.dumps(display, ensure_ascii=False), flush=True)
    return compact


# 在四十五分钟上限内推进生产流程，按审查期限等待并在终态或需人工介入时结束。
def drive():
    deadline = time.monotonic() + 2700
    while time.monotonic() < deadline:
        result = advance()
        if result['state'] in ('completed', 'failed', 'paused', 'needs_input', 'check_failed', 'needs_evidence'):
            return
        review = result.get('stage_review') or {}
        delay = max(1, min(55, review.get('deadline', time.time() + 10) - time.time() + .5))
        time.sleep(delay)
    raise TimeoutError('Rectification driver stopped at its bounded runtime')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=('migrate', 'partitions', 'start', 'advance', 'drive'))
    args = parser.parse_args()
    {'migrate': migrate, 'partitions': repair_partitions, 'start': start, 'advance': advance, 'drive': drive}[args.phase]()
