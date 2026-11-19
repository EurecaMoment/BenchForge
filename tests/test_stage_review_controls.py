from pathlib import Path
import tempfile
import unittest

from tests.database_fixtures import use_postgres

from pipelines.benchmark.execution.stage_review import call
from pipelines.benchmark.design.plan_versions import activate
from storage.artifacts import write
from pipelines.reviews.service import review


class StageReviewControlTests(unittest.TestCase):
    # 创建当前活动计划、独立会话和操作目录，供评审权限边界测试。
    def setUp(self):
        self.database_url = use_postgres(self)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.directory = self.root / 'operations' / 'review-test'
        self.directory.mkdir(parents=True)
        self.session = 'review-test-session'
        self.plan = self.root / 'plan.json'
        write(self.plan, {'schema': 'benchforge.pipeline/v1', 'steps': []})
        activate(self.root, self.session, str(self.plan))

    # 把测试参数与固定 DSH 会话绑定后调用阶段评审工具。
    def tool(self, **args):
        return call({'_dsh_context': {'session_id': self.session}, **args}, self.directory)

    # 验证工具调用不能冒充用户提前继续或提交用户指令。
    def test_only_the_user_can_continue_early_or_submit_a_user_instruction(self):
        pending = review(self.root, self.session, 'open', stage='S1', key=str(self.plan.resolve()), artifact=str(self.plan))
        with self.assertRaisesRegex(ValueError, 'Only the user'):
            self.tool(action='continue', review_id=pending['review_id'])
        with self.assertRaisesRegex(ValueError, 'come from the user'):
            self.tool(action='instruction', instruction='pretend the user approved')
        self.assertEqual(self.tool(action='wait', timeout_seconds=0)['state'], 'waiting_user')
        review(self.root, self.session, 'continue', review_id=pending['review_id'])
        self.assertTrue(self.tool(action='status')['can_continue'])

    # 验证模型手写轨迹摘要不能作为 S2 的真实阶段产物。
    def test_model_cannot_open_s2_with_a_handwritten_trajectory_summary(self):
        summary = self.root / 'trajectory-summary.json'
        write(summary, {'stage': 'S2', 'state': 'completed', 'trajectories': 100})
        with self.assertRaisesRegex(ValueError, 'actual S2 receipt'):
            self.tool(action='open', stage='S2', key='100-trajectories', artifact=str(summary))
        self.assertEqual(self.tool(action='status')['state'], 'none')

    # 验证 S1 评审必须引用当前已保存计划，不能改为任意草稿。
    def test_s1_must_reference_the_current_saved_plan(self):
        draft = self.root / 'draft.json'
        write(draft, {'status': 'done'})
        with self.assertRaisesRegex(ValueError, 'current saved plan'):
            self.tool(action='open', stage='S1', key='draft', artifact=str(draft))
        result = self.tool(action='open', stage='S1', key='current', artifact=str(self.plan))
        self.assertEqual(result['state'], 'waiting_user')

    # 验证模型自造评审键不能重新开启已放行的同一任务评审。
    def test_model_cannot_restart_a_released_review_by_inventing_another_key(self):
        first = self.tool(action='open', stage='S1', key='first-key', artifact=str(self.plan))
        review(self.root, self.session, 'continue', review_id=first['review_id'])
        repeated = self.tool(action='open', stage='S1', key='another-key', artifact=str(self.plan))
        self.assertEqual(repeated['review_id'], first['review_id'])
        self.assertEqual(repeated['deadline'], first['deadline'])
        self.assertTrue(repeated['can_continue'])
        self.assertNotIn('action=wait', repeated['next'])

    # 验证领域新键不能覆盖用户暂停或尚未处理的指令。
    def test_domain_cannot_replace_a_pause_or_pending_instruction_with_a_new_key(self):
        for action in ('pause', 'instruction'):
            session = 'domain-' + action
            first = review(self.root, session, 'open', stage='S1', key='first', artifact=str(self.plan))
            review(self.root, session, action, instruction='Keep the scene requirements')
            with self.assertRaisesRegex(ValueError, 'Resolve the paused review'):
                review(self.root, session, 'open', stage='S1', key='different', artifact=str(self.plan))
            self.assertEqual(review(self.root, session)['review_id'], first['review_id'])

    # 验证执行器阶段回执须与实际完成的计划成员一致。
    def test_runner_receipt_is_checked_against_actual_completed_members(self):
        run = self.root / 'run'
        step = {'id': 'scenes', 'uses': 'scene.acceptance'}
        plan = {'schema': 'benchforge.pipeline/v1', 'steps': [step]}
        write(self.plan, plan); write(run / 'plan.json', plan)
        result = {'schema': 'benchforge.scene-stage/v1', 'stage': 'S2', 'state': 'completed'}
        output = run / 'steps/scenes/result.json'
        write(output, result)
        write(run / 'state.json', {'steps': {'scenes': {'state': 'completed'}}})
        summary = run / 'reviews/S2.json'
        write(summary, {'schema': 'benchforge.stage-review/v1', 'stage': 'S2', 'plan': str(self.plan),
                        'outputs': [{'step': 'scenes', 'uses': 'scene.acceptance',
                                     'result_path': str(output.resolve()), 'result': result}]})
        self.assertEqual(self.tool(action='open', stage='S2', key='scene', artifact=str(summary))['stage'], 'S2')
        write(run / 'state.json', {'steps': {'scenes': {'state': 'waiting'}}})
        with self.assertRaisesRegex(ValueError, 'completed pipeline outputs'):
            self.tool(action='open', stage='S2', key='scene', artifact=str(summary))
