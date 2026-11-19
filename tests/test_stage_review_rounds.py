"""Real receipts and controlled clocks, never a model-supplied pass counter."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import threading
import unittest

from tests.database_fixtures import use_postgres
from database.engine import engine_for, workspace_id
from database.schema import reviews
from sqlalchemy import select, text
from unittest.mock import patch

from pipelines.benchmark.design.plan_versions import activate
from pipelines.reviews import service as review_service
from storage import stage_reviews as ledger
from storage.artifacts import write


class ReviewRoundTests(unittest.TestCase):
    # 建立可控时间下的真实评审计划及首次检查回执，隔离轮次状态测试。
    def setUp(self):
        self.database_url = use_postgres(self)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.session = 'round-test'
        self.plan = self.root / 'plan.json'
        write(self.plan, {'schema': 'benchforge.pipeline/v1', 'steps': []})
        activate(self.root, self.session, str(self.plan))
        self.first = self.call('open', now=1000, stage='S1', key=str(self.plan.resolve()), artifact=str(self.plan))

    # 以固定会话调用统一评审服务，允许指定动作及测试时钟。
    def call(self, action='status', **options):
        return review_service.review(self.root, self.session, action, **options)

    # 验证必须完成三轮真实检查及等待，且最终只放行一次。
    def test_three_real_checks_and_waits_release_once(self):
        self.assertTrue(self.first['last_check']['passed'])
        self.assertEqual(self.first['checks_completed'], 1)
        for number, clock in enumerate((1060, 1120, 1180), 1):
            before = self.call(now=clock - .1)
            self.assertFalse(before['can_continue'])
            value = self.call(now=clock)
            self.assertEqual(value['waits_completed'], number)
            self.assertEqual(value['checks_completed'], min(3, number + 1))
        self.assertEqual(value['state'], 'elapsed')
        self.assertTrue(value['can_continue'])
        self.assertEqual(self.call(now=5000)['rounds'], value['rounds'])
        self.assertEqual([row['check']['checked_at'] for row in value['rounds']], [1000, 1060, 1120])

    # 验证重复或并发读取不会加速等待，也不会重复记入检查轮次。
    def test_repeated_concurrent_reads_do_not_accelerate_or_duplicate_rounds(self):
        for clock, waits in ((1001, 0), (1060, 1), (1061, 1)):
            with ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(lambda _: self.call(now=clock), range(24)))
            result = self.call(now=clock)
            self.assertEqual(result['waits_completed'], waits)
            self.assertEqual(result['checks_completed'], waits + 1)

    # 验证服务停机时间不虚构检查，也不把新等待起点回溯到过去。
    def test_downtime_does_not_fabricate_missing_checks_or_backdate_waits(self):
        value = self.call(now=2000)
        self.assertEqual(value['checks_completed'], 2)
        self.assertEqual(value['waits_completed'], 1)
        self.assertEqual(value['deadline'], 2060)
        self.assertFalse(value['can_continue'])

    # 验证真实产物检查失败后，即使超时或用户继续仍不能跳过失败。
    def test_failed_real_artifact_check_blocks_even_after_deadline_and_user_continue(self):
        self.plan.unlink()
        result = self.call(now=1060)
        self.assertEqual(result['state'], 'check_failed')
        self.assertFalse(result['can_continue'])
        self.assertIn('existing receipt', result['check_failure']['error'])
        self.assertEqual(self.call(now=5000)['state'], 'check_failed')
        self.assertEqual(self.call('continue', now=5000)['state'], 'check_failed')
        write(self.plan, {'schema': 'benchforge.pipeline/v1', 'steps': []})
        # The failed Continue was a user instruction, so a successful repair
        # may fulfill it; no completed self-check rounds are invented.
        repaired = self.call('retry', now=5001)
        self.assertEqual(repaired['state'], 'continued')
        self.assertEqual(repaired['waits_completed'], 0)

    # 验证同契约修复复用已完成检查轮次及当前等待截止时间。
    def test_repair_reuses_completed_rounds_and_current_deadline(self):
        value = self.call(now=1060)
        repaired = self.root / 'repair.json'
        write(repaired, {'schema': 'benchforge.pipeline/v1', 'steps': [], 'stage_review_key': self.first['key']})
        activate(self.root, self.session, str(repaired))
        changed = self.call('open', now=1070, stage='S1', key=self.first['key'], artifact=str(repaired))
        self.assertEqual(changed['review_id'], self.first['review_id'])
        self.assertEqual(changed['deadline'], value['deadline'])
        self.assertEqual(changed['waits_completed'], 1)
        self.assertEqual(changed['checks_completed'], 2)
        self.assertNotEqual(changed['last_check']['digest'], value['last_check']['digest'])

    # 验证检查进行中用户提交暂停或指令时，迟到回执不能覆盖用户状态。
    def test_pause_and_instruction_win_a_race_with_check_completion(self):
        for action in ('pause', 'instruction'):
            with self.subTest(action=action):
                self.call('continue', now=1001) if action == 'instruction' else None
                if action == 'instruction':
                    # A distinct active revision for the second race.
                    write(self.plan, {'schema': 'benchforge.pipeline/v1', 'steps': [], 'stage_review_key': 'second'})
                    self.first = self.call('open', now=1002, stage='S1', key='second', artifact=str(self.plan))
                entered, finish = threading.Event(), threading.Event()
                # 阻塞模拟产物检查直到并发用户操作完成，再返回通过回执以检验版本保护。
                def check(*_):
                    entered.set()
                    self.assertTrue(finish.wait(5))
                    return {'artifact': str(self.plan), 'digest': 'checked', 'checks': ['test-race']}
                with patch.object(review_service, 'check_artifact', side_effect=check), ThreadPoolExecutor(max_workers=1) as pool:
                    task = pool.submit(self.call, now=1100)
                    self.assertTrue(entered.wait(5))
                    paused = ledger.review(self.root, self.session, action, now=1100, instruction='Keep all requirements')
                    finish.set()
                    self.assertEqual(task.result()['state'], paused['state'])
                self.assertFalse(self.call(now=9999)['can_continue'])

    # 验证待处理指令未应用前，用户继续也不能使其被跳过。
    def test_pending_instruction_cannot_be_user_continued_without_application(self):
        self.call('instruction', now=1059, instruction='Retain every sensor')
        self.assertEqual(self.call(now=9999)['state'], 'needs_input')
        with self.assertRaisesRegex(ValueError, 'acknowledge'):
            self.call('continue', now=9999)
        value = self.call('acknowledge', now=1061)
        self.assertFalse(value['can_continue'])
        self.assertEqual(value['checks_completed'], 1)
        self.assertEqual(value['deadline'], 1060)

    # 验证确认指令已应用不会撤销用户明确暂停。
    def test_acknowledging_an_instruction_does_not_undo_an_explicit_user_pause(self):
        self.call('pause', now=1020)
        self.call('instruction', now=1030, instruction='Repair the draft while paused')
        self.assertEqual(self.call('acknowledge', now=5000)['state'], 'paused')
        self.assertFalse(self.call(now=9999)['can_continue'])
        self.assertTrue(self.call('continue', now=10000)['can_continue'])

    # 验证存储层读取不能仅因墙钟时间已过就自行批准评审。
    def test_plain_storage_reader_cannot_approve_by_wall_clock(self):
        result = ledger.review(self.root, self.session, now=9999)
        self.assertEqual(result['state'], 'waiting_user')
        self.assertEqual(result['checks_completed'], 1)
        self.assertFalse(result['can_continue'])

    # 验证无效评估方案必须真实自检，不能靠模型写入 passed 标志通过。
    def test_invalid_saved_assessment_is_checked_not_accepted_as_a_model_pass_flag(self):
        write(self.plan, {'schema': 'benchforge.pipeline/v1', 'steps': [],
                         'assessment': {'passed': True}, 'self_check_passed': True})
        value = self.call(now=1060)
        self.assertEqual(value['state'], 'check_failed')
        self.assertFalse(value['can_continue'])
        self.assertIn('Plan needs corrections', value['check_failure']['error'])

    # 验证旧评审策略按当前会话迁移，不改写其他会话记录。
    def test_old_policy_is_migrated_without_rewriting_other_sessions(self):
        legacy = self.root / 'legacy'
        plan = legacy / 'plan.json'
        write(plan, {'schema': 'benchforge.pipeline/v1', 'steps': []})
        activate(legacy, self.session, str(plan))
        with engine_for(self.database_url).begin() as db:
            db.execute(reviews.insert(), [
                dict(workspace_id=workspace_id(legacy), session=self.session, key=str(plan.resolve()),
                     stage='S1', artifact=str(plan), started=100, deadline=400, state='waiting_user', instruction=''),
                dict(workspace_id=workspace_id(legacy), session='another-session', key='another-plan',
                     stage='S1', artifact='elsewhere.json', started=100, deadline=400, state='paused', instruction='Keep paused')])
        value = review_service.review(legacy, self.session, now=1000)
        self.assertEqual((value['checks_completed'], value['waits_completed'], value['deadline']), (1, 0, 1060))
        with engine_for(self.database_url).connect() as db:
            self.assertEqual(tuple(db.execute(select(reviews.c.state,reviews.c.instruction).where(
                reviews.c.session=='another-session')).one()), ('paused', 'Keep paused'))


    # 验证被取代评审的回执不会唤醒或修改当前评审。
    def test_superseded_receipt_never_wakes_or_mutates_current_review(self):
        old = self.first['review_id']
        newer = self.call('open', now=1030, stage='S1', key='changed-contract', artifact=str(self.plan))
        self.assertEqual(self.call(now=2000, review_id=old)['state'], 'superseded')
        with self.assertRaisesRegex(ValueError, 'superseded'):
            self.call('continue', now=2000, review_id=old)
        self.assertEqual(self.call(now=1031)['review_id'], newer['review_id'])

    # 验证旧待审状态迁移不虚构检查轮次，并保留已结束历史。
    def test_migrate_legacy_pending_without_inventing_rounds_and_keep_finished_history(self):
        with engine_for(self.database_url).begin() as db:
            db.execute(text("UPDATE reviews SET policy_version=0,progress='{}',deadline=900"))
        value = self.call(now=2000)
        self.assertEqual(value['state'], 'waiting_user')
        self.assertEqual(value['waits_completed'], 0)
        self.assertEqual(value['checks_completed'], 1)
        self.assertEqual(value['deadline'], 2060)
        with engine_for(self.database_url).begin() as db:
            db.execute(text("UPDATE reviews SET policy_version=0,progress='{}',state='elapsed'"))
        self.assertTrue(self.call(now=2001)['can_continue'])


if __name__ == '__main__':
    unittest.main()
