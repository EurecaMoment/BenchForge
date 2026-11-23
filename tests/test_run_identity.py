"""Task identity survives retries; execution evidence remains bound to its inputs."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from pipelines.benchmark.execution.run_identity import run_root, validate_rebinding
from storage.artifacts import write


class RunIdentityTests(TestCase):
    # 验证重复执行和来源改名共享同一评审运行，不依赖最新目录猜测。
    def test_review_identity_selects_one_run_across_tool_calls(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            key = root / 'operations/first/plan.json'
            plan = {'stage_review_key': str(key), 'production_contract': 'benchforge.production-stages/v1'}
            revised = root / 'operations/revision/plan.json'
            write(key, plan)
            write(revised, plan)
            args = {'plan': str(key), '_workspace': str(root), '_dsh_context': {'session_id': 'one'}}
            expected = key.parent / 'pipeline_run'
            self.assertEqual(run_root(args, root / 'operations/call1'), expected)
            self.assertEqual(run_root({**args, 'plan': str(revised)}, root / 'operations/call2'), expected)
            explicit = root / 'independent-run'
            self.assertEqual(run_root({**args, 'run': str(explicit)}, root), explicit)

    # 验证跨工作区的评审路径不能决定运行写入位置。
    def test_review_identity_must_stay_in_workspace(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            path = root / 'plan.json'
            write(path, {'stage_review_key': str(root.parent / 'foreign/plan.json'),
                         'production_contract': 'benchforge.production-stages/v1'})
            with self.assertRaisesRegex(ValueError, 'conversation workspace'):
                run_root({'plan': str(path), '_workspace': str(root),
                          '_dsh_context': {'session_id': 'one'}}, root)

    # 验证仅改变来源元数据可以保留已完成结果，真实输入变化不能套用旧验收。
    def test_rebinding_does_not_reuse_completed_evidence_after_input_repairs(self):
        original = {'stage_review_key': 'approved-plan.json', 'inputs': {},
                    'assessment': {'scenes': {'designs': [{'id': 'room', 'summary': 'Original'}]}},
                    'authoring_sources': {'request_path': 'request.json'}}
        revised = deepcopy(original)
        revised['authoring_sources']['request_path'] = 'request_rev2.json'
        completed = {'steps': {'scene': {'state': 'completed'}}}
        validate_rebinding(original, revised, completed)
        revised['assessment']['scenes']['designs'][0]['summary'] = 'Repaired scene'
        validate_rebinding(original, revised, {'steps': {'scene': {'state': 'needs_evidence'}}})
        with self.assertRaisesRegex(ValueError, 'completed steps'):
            validate_rebinding(original, revised, completed)
        revised['inputs']['quantity_contract'] = 'different'
        with self.assertRaisesRegex(ValueError, 'requirements changed'):
            validate_rebinding(original, revised, {})

    # 验证显式新任务即使正文相同，也不能继承旧运行。
    def test_new_review_cannot_rebind_an_old_run(self):
        before = {'stage_review_key': 'one', 'inputs': {}}
        after = {**before, 'stage_review_key': 'two'}
        with self.assertRaisesRegex(ValueError, 'requirements changed'):
            validate_rebinding(before, after, {})
