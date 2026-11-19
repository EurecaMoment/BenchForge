"""服务合同失败保留归属并实时重查，禁止消耗场景几何修复次数。"""
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import unittest
from unittest.mock import Mock, patch

from capabilities.isaac_sim.scene_requirements import UnsupportedScene
from pipelines.scene_production.lifecycle import SceneLifecycle
from pipelines.scene_production import repair
from pipelines.scene_production.scheduler import coordinate
from pipelines.scene_production.service_failures import (
    SERVICE_OWNER, SERVICE_STAGE, is_service_failure, stage_failure,
)
from service_access.scene_service import ScenePreflightRejected


def service_issue():
    """构造超过通用错误摘要长度的结构化问题，验证详情不会被截断。"""
    return {'code': 'service_contract_mismatch', 'path': '/objects/robot',
            'message': 'schema mismatch ' * 120, 'repair_owner': SERVICE_OWNER,
            'retry_unchanged': False, 'severity': 'error', 'blocking': True,
            'unsupported_fields': ['/physics/static_friction', '/mass_kg']}


class CaptureServiceRecoveryTests(unittest.TestCase):
    def setUp(self):
        """为原始程序与冻结采集准备隔离目录，不访问网络或数据库。"""
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.program = {'objects': [], 'cameras': [{'id': 'camera'}]}
        self.settings = {'grpc': {'host': 'fixture', 'port': 50051}}
        (self.root / 'program.json').write_text(json.dumps(self.program))
        frozen = self.root / 'isaac_sim/attempt_0/capture.json'
        frozen.parent.mkdir(parents=True)
        frozen.write_text(json.dumps({'settings': self.settings}))
        self.error = UnsupportedScene('service mismatch', issues=[service_issue()])
        self.task = {'id': 'run.scene', 'unit': {'phase': 'capture', 'revision': 0,
                     'intent': {'capture_options': {'view_indices': [], 'export_scene': False}}},
                     'failure': stage_failure(self.error, 'capture')}

    def directory(self, task_id, revision):
        """让生命周期在同一隔离目录读取原程序与冻结回执。"""
        return self.root

    def test_scheduler_persists_complete_service_issues(self):
        """经过真实调度异常分支后，所有问题与归属仍完整持久化。"""
        stop, store = Event(), Mock()
        pending = {**self.task, 'run_id': 'run', 'state': 'READY'}
        store.all_work.return_value = [pending]
        store.claim_stage.return_value = {**pending, 'state': 'RUNNING'}
        store.renew_stage.return_value = True
        store.snapshot.return_value = {'state': 'RUNNING'}
        store.update.side_effect = lambda *args, **kwargs: stop.set()
        with patch('pipelines.scene_production.scheduler.traceback.print_exc'):
            coordinate(store, stop, plan_scene=Mock(), review_scene=Mock(),
                       policy_for=Mock(return_value={}), capture_only_review=Mock(),
                       capture_scene=Mock(side_effect=self.error))
        failure = store.update.call_args.kwargs['failure']
        self.assertEqual(failure['stage'], SERVICE_STAGE)
        self.assertEqual(failure['issues'], self.error.issues)
        self.assertEqual(failure['repair_owner'], SERVICE_OWNER)
        self.assertFalse(failure['retry_unchanged'])
        self.assertGreater(len(failure['issues'][0]['message']), 1000)

    def test_legacy_execution_error_strings_keep_transport_semantics(self):
        """旧失败字符串即使提到服务错配，也不能自动升级为结构化服务阻断。"""
        failure = stage_failure(OSError('service_contract_mismatch'), 'capture')
        self.assertEqual(failure['stage'], 'capture_execution')
        self.assertFalse(is_service_failure(failure))
        self.task['failure'] = failure
        with (patch('pipelines.scene_production.lifecycle.policy_for', return_value={}),
              patch('pipelines.scene_production.lifecycle.verify_capture_service_recovery') as live):
            unit = SceneLifecycle(self.root).retry(self.task, None, 'resume', self.directory)
        live.assert_not_called()
        self.assertEqual(unit['capture_generation'], 0)
        self.assertEqual(unit['capture_recovery_mode'], 'resume')

    def test_retry_rechecks_exact_scope_and_recovers_after_service_restoration(self):
        """同一场景仍不兼容时不能重试，服务恢复后实时检查通过即可恢复。"""
        original = deepcopy(self.task)
        with (patch('pipelines.scene_production.lifecycle.policy_for', return_value={}),
              patch('pipelines.scene_production.service_failures.current_capture_settings',
                    return_value=self.settings),
              patch('capabilities.isaac_sim.capture_contract.inspect_live_capture_service',
                    side_effect=[[service_issue()], []]) as live):
            with self.assertRaises(UnsupportedScene):
                SceneLifecycle(self.root).retry(self.task, None, 'auto', self.directory)
            unit = SceneLifecycle(self.root).retry(self.task, None, 'auto', self.directory)
        self.assertEqual(unit['phase'], 'capture')
        self.assertEqual(unit['revision'], 0)
        self.assertEqual(unit['capture_generation'], 0)
        self.assertEqual(self.task, original)
        self.assertEqual(live.call_args.kwargs['scope']['view_indices'], [])
        self.assertFalse(live.call_args.kwargs['scope']['scene_export_requested'])

    def test_service_recovery_keeps_production_admission(self):
        """生产服务恢复仍需重新静态准入，不绕过原有生产约束。"""
        with (patch('pipelines.scene_production.lifecycle.policy_for', return_value={'enabled': True}),
              patch('pipelines.scene_production.lifecycle.verify_capture_service_recovery', return_value=False),
              patch('capabilities.scene_authoring.contracts.scene_design.capture_retry_directory', return_value=self.root),
              patch('pipelines.scene_production.lifecycle.admit_program') as admission):
            unit = SceneLifecycle(self.root).retry(self.task, None, 'auto', self.directory)
        admission.assert_called_once()
        self.assertEqual(unit['phase'], 'capture')

    def test_changed_service_configuration_starts_a_new_frozen_attempt(self):
        """配置已修复时开启新尝试，保留旧冻结输入及原场景不变。"""
        frozen_path = self.root / 'isaac_sim/attempt_0/capture.json'
        original = frozen_path.read_bytes()
        with (patch('pipelines.scene_production.lifecycle.policy_for', return_value={}),
              patch('pipelines.scene_production.service_failures.current_capture_settings',
                    return_value={'grpc': {'host': 'recovered', 'port': 50051}}),
              patch('capabilities.isaac_sim.capture_contract.inspect_live_capture_service', return_value=[])):
            unit = SceneLifecycle(self.root).retry(self.task, None, 'auto', self.directory)
        self.assertEqual(unit['capture_generation'], 1)
        self.assertEqual(unit['capture_recovery_mode'], 'restart')
        self.assertEqual(frozen_path.read_bytes(), original)

    def test_service_failure_cannot_be_routed_to_scene_feedback(self):
        """服务故障不接受以场景反馈重新规划来规避核对。"""
        with patch('pipelines.scene_production.lifecycle.verify_capture_service_recovery') as live:
            with self.assertRaisesRegex(ValueError, 'scene repair feedback'):
                SceneLifecycle(self.root).retry(self.task, 'remove physics', 'auto', self.directory)
        live.assert_not_called()

    def test_http_preflight_failure_keeps_structured_ownership(self):
        """HTTP 准入异常也保留完整归属，供修复批次判断服务等待。"""
        error = ScenePreflightRejected({'issues': [service_issue()]})
        failure = stage_failure(error, 'capture')
        self.assertTrue(is_service_failure(error))
        self.assertEqual(failure['issues'], [service_issue()])
        self.assertEqual(failure['stage'], SERVICE_STAGE)

    def test_repair_stops_before_model_on_service_preflight_failure(self):
        """基线实时预检发现服务错配时不调用模型，也不修改场景。"""
        report = {'issues': [service_issue()], 'passed': False}
        row = {'id': 'source', 'name': 'scene', 'source_directory': str(self.root)}
        original = (self.root / 'program.json').read_bytes()
        with (patch.object(repair, 'inspect_capture_program', return_value=report),
              patch.object(repair, 'author_completion') as author):
            with self.assertRaises(UnsupportedScene) as rejected:
                repair.revise({'artifacts': str(self.root)}, row, self.root / 'repair')
        author.assert_not_called()
        self.assertEqual((self.root / 'program.json').read_bytes(), original)
        self.assertEqual(rejected.exception.issues, [service_issue()])

    def test_waiting_capture_uses_server_recheck_without_spending_repair_attempt(self):
        """等待采集调用原任务实时恢复，服务未恢复不消耗几何修复次数。"""
        record = {'state': 'capturing', 'attempt': 1, 'run_id': 'run', 'task_id': 'run.scene'}
        repair.wait_for_service(record, self.task['failure'], 'capturing')
        post = Mock(side_effect=[{'tasks': [{'state': 'FAILED_FINAL'}]}, self.error])
        self.assertFalse(repair.resume_service_wait(record, {}, post))
        self.assertEqual(record['state'], 'waiting_service')
        self.assertEqual(record['attempt'], 1)
        post.side_effect = [{'tasks': [{'state': 'FAILED_FINAL'}]}, {}]
        self.assertTrue(repair.resume_service_wait(record, {}, post))
        self.assertEqual(record['state'], 'capturing')
        self.assertEqual(record['attempt'], 1)
        self.assertEqual(post.call_args.args, ('/retry', {'task_id': 'run.scene'}))

    def test_waiting_candidate_restores_original_feedback_and_attempt(self):
        """服务恢复后继续同一候选修复次数，保留服务出现前的几何反馈。"""
        original_feedback = {'issues': ['floating object']}
        record = {'state': 'needs_repair', 'attempt': 0, 'pending_repair_attempt': 1,
                  'program_path': str(self.root / 'program.json'), 'failure': original_feedback}
        repair.wait_for_service(record, self.error, 'needs_repair')
        with (patch.object(repair, 'current_capture_settings', return_value=self.settings),
              patch.object(repair, 'require_live_service', side_effect=[self.error, None]) as live):
            self.assertFalse(repair.resume_service_wait(record, {}, Mock()))
            self.assertTrue(repair.resume_service_wait(record, {}, Mock()))
        self.assertEqual(live.call_count, 2)
        self.assertEqual(record['state'], 'needs_repair')
        self.assertEqual(record['failure'], original_feedback)
        self.assertEqual(record['attempt'], 0)
        self.assertEqual(record['pending_repair_attempt'], 1)

    def run_repair_batch_once(self, record, response):
        """驱动一次真实批次循环并保存状态，隔离审核、HTTP 和后续轮询。"""
        batch = self.root / 'batch'
        repair.save(batch / 'targets.json', [{'id': 'source', 'name': 'scene',
                    'source_directory': str(self.root)}])
        status = batch / 'source/status.json'
        repair.save(status, record)
        config = {'artifacts': str(self.root), 'operator_token': 'fixture'}
        request = Mock(side_effect=response) if isinstance(response, Exception) else Mock(return_value=response)
        arguments = ['repair', '--config', str(self.root / 'config.json'),
                     '--workspace', str(self.root), '--directory', str(batch),
                     '--session', 'fixture', '--action', 'execute']
        with (patch('sys.argv', arguments),
              patch('service_access.runtime_config.load_service_config', return_value=config),
              patch('service_access.runtime_config.apply_runtime_environment'),
              patch('service_access.scene_service.post_service_json', request),
              patch.object(repair, 'review', return_value={'can_continue': True}),
              patch.object(repair, 'revise') as revise,
              patch.object(repair.time, 'sleep', side_effect=StopIteration)):
            with self.assertRaises(StopIteration):
                repair.main()
        revise.assert_not_called()
        return json.loads(status.read_text())

    def test_batch_http_admission_failure_waits_instead_of_repairing(self):
        """批次真实提交分支将 HTTP 服务阻断保存为等待，避免发送给修复模型。"""
        record = {'state': 'planned', 'attempt': 0, 'name': 'scene',
                  'program_path': str(self.root / 'program.json'),
                  'plan_path': str(self.root / 'plan.json'), 'stage_review': {'review_id': 'review'}}
        result = self.run_repair_batch_once(record, ScenePreflightRejected({'issues': [service_issue()]}))
        self.assertEqual(result['state'], 'waiting_service')
        self.assertEqual(result['service_resume_state'], 'planned')
        self.assertEqual(result['attempt'], 0)
        self.assertEqual(result['failure']['issues'], [service_issue()])

    def test_batch_terminal_capture_failure_waits_instead_of_repairing(self):
        """批次读取真实失败任务分支后保留合同归属，不计为场景缺陷。"""
        record = {'state': 'capturing', 'attempt': 1, 'name': 'scene',
                  'run_id': 'run', 'task_id': 'run.scene'}
        task = {**self.task, 'state': 'FAILED_FINAL'}
        result = self.run_repair_batch_once(record, {'tasks': [task]})
        self.assertEqual(result['state'], 'waiting_service')
        self.assertEqual(result['service_resume_state'], 'capturing')
        self.assertEqual(result['attempt'], 1)
        self.assertEqual(result['failure']['issues'], self.error.issues)


if __name__ == '__main__':
    unittest.main()
