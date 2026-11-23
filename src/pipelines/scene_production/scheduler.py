"""Persistent stage scheduler; scene planning remains in the engine facade."""
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from threading import Thread

from sqlalchemy.exc import OperationalError
from sqlalchemy.exc import TimeoutError as DatabaseTimeout

from capabilities.asset_generation.generation import GenerationTools
from capabilities.asset_generation.generation import ResourceWait
from service_access.request_context import RunCancelled
from storage.scene_jobs.capture_budget import CaptureBudgetExhausted
from storage.scene_jobs.store import uid
from pipelines.scene_production.service_failures import stage_failure
from pipelines.scene_production.stage_control import StageControl


class _StageStore:
    """Fence writes from a claimed stage by its persisted attempt owner."""

    # 把本次阶段执行的存储访问绑定到租约所有者。
    def __init__(self, store, owner):
        self._store = store
        self._owner = owner

    # 将未包装的存储方法透传给底层仓库。
    def __getattr__(self, name):
        return getattr(self._store, name)

    # 为状态更新自动附加预期租约所有者，阻止过期执行者修改任务。
    def update(self, task_id, **fields):
        return self._store.update(task_id, expected_owner=self._owner, **fields)

    def reserve_capture(self, task_id, token, program, *, retained=False):
        return self._store.reserve_capture(task_id, token, program, expected_owner=self._owner,
                                           retained=retained)

    # 在采集发布时强制校验本执行者的租约身份。
    def publish_capture(self, task_id, token, incoming):
        return self._store.publish_capture(task_id, token, incoming, expected_owner=self._owner)


# 识别无需资源生成的程序准备和只采集评审任务，允许走独立并发通道。
def capture_handoff(unit):
    """CPU-only preparation/completion should not queue behind asset generation."""
    if unit['phase'] == 'review':
        return unit['intent'].get('capture_only', False)
    if unit['phase'] != 'plan' or not unit.get('submitted_program'):
        return False
    if unit.get('quality_repairs') or unit.get('operator_feedback'):
        return False
    if any(unit['intent'].get(key) for key in ('layout', 'parent_task_id', 'refine_task_id')):
        return False
    return not any(
        obj['kind'] == 'generated'
        for obj in unit['submitted_program']['program']['objects']
    )


# 按阶段资源及并发预算领取任务，维护租约并在停止时回收执行资源。
def coordinate(
    store,
    stop,
    *,
    plan_scene,
    review_scene,
    policy_for,
    capture_only_review,
    capture_handoff=capture_handoff,
    generation_tools=GenerationTools,
    generated_asset_reviewer=None,
    resource_wait=ResourceWait,
    capture_scene=None,
    capture_concurrency=4,
):
    """Run work only after durable single-owner stage acquisition."""

    # 执行已领取阶段，持续续约并将取消或租约失效传播到规划、采集和评审。
    def execute(claimed_task, owner):
        stage_store = _StageStore(store, owner) if owner else store
        task_stop = StageControl(store, claimed_task, owner, stop)
        lease_thread = None
        if owner and callable(getattr(store, 'renew_stage', None)):
            lease_thread = Thread(target=task_stop.renew, daemon=True)
            lease_thread.start()
        try:
            try:
                task = claimed_task
                phase = task['unit']['phase']
                if phase == 'capture':
                    if capture_scene is None:
                        raise ValueError('Native Isaac Sim capture stage is not configured')
                    capture_scene(stage_store, task, task_stop)
                elif phase == 'layout':
                    from capabilities.asset_generation.layout import execute_design

                    execute_design(stage_store, task, task_stop)
                elif phase == 'generation':
                    from capabilities.asset_generation.generation_stage import execute_generation_stage

                    execute_generation_stage(stage_store, task, task_stop)
                elif phase == 'asset':
                    directory = stage_store.directory(task['id'], task['unit']['revision'])
                    intent = task['unit']['intent']
                    spec = {
                        'label': intent.get('label', intent['name']),
                        'prompt': intent['description'],
                    }
                    spec.update(
                        {
                            key: value
                            for key, value in intent.items()
                            if key
                            in {
                                'source_image',
                                'source_mask',
                                'source_mesh',
                                'source_gaussian',
                                'asset_id',
                                'seed',
                                'source_up_axis',
                                'size_hint_m',
                                'subject_box',
                                'edit_reference',
                            }
                        }
                    )
                    tools = generation_tools(stage_store.root)
                    result = tools.generate_asset(spec, directory / 'generation', task_stop)
                    if not (spec.get('source_mesh') or spec.get('asset_id')):
                        if generated_asset_reviewer is None:
                            from capabilities.asset_generation.asset_review import review_generated_asset
                        else:
                            review_generated_asset = generated_asset_reviewer

                        result = review_generated_asset(
                            tools,
                            spec,
                            result,
                            directory / 'asset_review',
                            task_stop,
                            repair=False,
                        )
                    stage_store.update(
                        task['id'],
                        state='SUCCEEDED',
                        result=result,
                        unit={**task['unit'], 'phase': 'complete'},
                    )
                elif phase == 'dataset':
                    stage_store.update(
                        task['id'],
                        state='FAILED_FINAL',
                        failure={
                            'code': 'LEGACY_DATASET_RETIRED',
                            'detail': 'Use data.capability or submit a scene with target_items.',
                        },
                    )
                elif phase == 'review' and task['unit']['intent'].get('capture_only') and policy_for(
                    stage_store.root,
                    task['unit'].get('intent', {}).get('execution_mode'),
                ):
                    review_scene(stage_store, task, task_stop, capture_only=True)
                elif phase == 'review' and task['unit']['intent'].get('capture_only'):
                    directory = stage_store.directory(task['id'], task['unit']['revision'])
                    report = __import__('json').loads(
                        (directory / 'capture' / 'report.json').read_text()
                    )
                    files = [
                        {'file': path.relative_to(directory).as_posix(), 'source_path': str(path)}
                        for path in sorted((directory / 'capture').glob('*'))
                        if path.is_file()
                    ]
                    captured = report.get('status') == 'captured'
                    result = {
                        'operation': 'capture_only',
                        'directory': str(directory),
                        'files': files,
                        'report': report,
                        'scene_quality_assessed': False,
                        'data_exported': False,
                    }
                    stage_store.update(
                        task['id'],
                        state='SUCCEEDED' if captured else 'FAILED_FINAL',
                        result=result,
                        unit={**task['unit'], 'phase': 'complete' if captured else 'review'},
                        failure=None if captured else {'capture_report': report},
                    )
                else:
                    (plan_scene if phase == 'plan' else review_scene)(
                        stage_store, task, task_stop
                    )
            except CaptureBudgetExhausted as exc:
                stage_store.update(claimed_task['id'], state='FAILED_FINAL', failure=exc.failure())
            except RunCancelled as exc:
                if task_stop.canceled:
                    stage_store.update(
                        claimed_task['id'],
                        state='CANCELED',
                        failure={'stage': claimed_task['unit'].get('phase'), 'error': str(exc)},
                    )
            except resource_wait as exc:
                if task_stop.canceled:
                    stage_store.update(
                        claimed_task['id'],
                        state='CANCELED',
                        failure={'stage': claimed_task['unit'].get('phase'), 'error': str(exc)},
                    )
                else:
                    stage_store.update(
                        claimed_task['id'],
                        state='READY',
                        ready_at=time.time() + 30,
                        failure={'waiting_resource': str(exc)},
                    )
            except Exception as exc:
                traceback.print_exc()
                if task_stop.canceled:
                    stage_store.update(
                        claimed_task['id'],
                        state='CANCELED',
                        failure={'stage': claimed_task['unit'].get('phase'), 'error': str(exc)},
                    )
                elif not task_stop.detached():
                    stage_store.update(
                        claimed_task['id'],
                        state='FAILED_FINAL',
                        failure=stage_failure(exc, claimed_task['unit'].get('phase')),
                    )
        finally:
            task_stop.done.set()
            if lease_thread is not None:
                lease_thread.join(timeout=1)

    active = {}
    handoffs = {}
    captures = {}
    with ThreadPoolExecutor(max_workers=3, thread_name_prefix='production') as pool, ThreadPoolExecutor(
        max_workers=1, thread_name_prefix='capture-handoff'
    ) as capture_pool, ThreadPoolExecutor(max_workers=capture_concurrency,
                                          thread_name_prefix='isaac-sim-rpc') as simulation_pool:
        while not stop.is_set():
            active = {key: future for key, future in active.items() if not future.done()}
            handoffs = {key: future for key, future in handoffs.items() if not future.done()}
            captures = {key: future for key, future in captures.items() if not future.done()}
            try:
                work = store.all_work()
            except (DatabaseTimeout, OperationalError):
                traceback.print_exc()
                stop.wait(2)
                continue

            now = time.time()
            for task in work:
                if task['id'] in active or task['id'] in handoffs or task['id'] in captures:
                    continue
                phase = task['unit']['phase']
                if phase not in {'plan', 'review', 'asset', 'dataset', 'layout', 'generation', 'capture'}:
                    continue
                if task['state'] not in {'PENDING', 'READY', 'RUNNING'}:
                    continue
                if (task.get('ready_at') or 0) > now:
                    continue
                if task['state'] == 'RUNNING' and task.get('lease_expires_at') not in (None, 0):
                    if task['lease_expires_at'] > now:
                        continue

                lightweight = capture_handoff(task['unit'])
                try:
                    strict_review = policy_for(
                        store.root,
                        task['unit'].get('intent', {}).get('execution_mode'),
                    )
                except ValueError:
                    # Stage execution records a durable policy failure on the task.
                    strict_review = True
                if phase == 'review' and strict_review:
                    lightweight = False
                pending = captures if phase == 'capture' else handoffs if lightweight else active
                capacity = capture_concurrency if phase == 'capture' else 1 if lightweight else 3
                if len(pending) >= capacity:
                    continue

                owner = uid('stage')
                if callable(getattr(store, 'claim_stage', None)):
                    try:
                        claimed = store.claim_stage(task['id'], owner)
                    except (DatabaseTimeout, OperationalError):
                        # 未确认领取成功时等待下一轮，已有租约防止重复执行。
                        break
                    if not claimed:
                        continue
                    task = claimed
                else:
                    # Compatibility for isolated test stores and older adapters.
                    try:
                        store.update(task['id'], state='RUNNING', failure=None)
                    except TypeError:
                        continue
                    task = {**task, 'state': 'RUNNING'}
                    owner = None

                executor = simulation_pool if phase == 'capture' else capture_pool if lightweight else pool
                pending[task['id']] = executor.submit(
                    execute, task, owner
                )
            try:
                store.settle_runs()
            except (DatabaseTimeout, OperationalError):
                traceback.print_exc()
            stop.wait(2)
