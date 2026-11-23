"""完整请求文件与内联计划共享校验语义，控制开关不能变成业务字段冲突。"""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from pipelines.benchmark.design.plan_input import CONTROL_FIELDS, FIELDS, resolve_request, inherit_revision_inputs
from pipelines.benchmark.design.planner import plan


class PlanFileInputTests(unittest.TestCase):
    def test_revision_defaults_preserve_policies_without_overriding_explicit_changes(self):
        """省略字段继承活动计划；显式变更和独立新任务仍按原输入校验。"""
        previous = {'objective': 'Existing task', 'inputs': {'execution_mode': 'production', 'seed': 7},
                    'selections': [{'template_ids': ['C43-T01'], 'count': 10, 'reason': 'Distance'}]}
        args = {'template_ids': ['C43-T01'], 'inputs': {'seed': 9}}
        result, inherited = inherit_revision_inputs(args, previous)
        self.assertEqual(result['inputs'], {'seed': 9, 'execution_mode': 'production'})
        self.assertEqual(result['selections'], previous['selections'])
        self.assertNotIn('inputs.seed', inherited)
        self.assertEqual(args, {'template_ids': ['C43-T01'], 'inputs': {'seed': 9}})
        separate = {**args, 'new_task': True}
        self.assertEqual(inherit_revision_inputs(separate, previous), (separate, []))
        changed, _ = inherit_revision_inputs({'template_ids': ['C53-T01']}, previous)
        self.assertNotIn('selections', changed)

    def test_every_public_plan_field_is_accepted_by_the_file_transport(self):
        """逐项对账 DSH 公开计划参数，新增参数不能再次遗漏文件传输白名单。"""
        source = Path(__file__).resolve().parents[1] / 'src/dsh/tools/benchmark/contracts.mjs'
        schemas, _ = json.JSONDecoder().raw_decode(source.read_text(encoding='utf-8').split('export const schemas =', 1)[1].lstrip())
        self.assertEqual(set(schemas['pipeline_plan']) - {'request_path'}, FIELDS | CONTROL_FIELDS)

    def setUp(self):
        """创建仅包含当前测试计划的临时工作区。"""
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.request = self.root / 'request.json'
        self.body = {'objective': '无人机探索', 'template_ids': ['C43-T01'], 'validate_only': True}
        self.request.write_text(json.dumps(self.body), encoding='utf-8')

    def test_file_control_and_inline_override_preserve_the_saved_request(self):
        """文件默认只校验；显式布尔开关可切换为保存，无需重写计划文件。"""
        original = self.request.read_bytes()
        for inline, expected in (({}, True), ({'validate_only': True}, True), ({'validate_only': False}, False)):
            with self.subTest(inline=inline):
                result, sources = resolve_request({'request_path': str(self.request), **inline,
                    '_dsh_context': {'session_id': 'trusted'}}, self.root)
                self.assertIs(result['validate_only'], expected)
                self.assertEqual(result['_dsh_context'], {'session_id': 'trusted'})
                self.assertEqual(result['objective'], self.body['objective'])
                self.assertEqual(sources, {'request_path': str(self.request.resolve())})
        self.assertEqual(self.request.read_bytes(), original)

    def test_control_is_boolean_and_unknown_or_conflicting_fields_remain_rejected(self):
        """兼容开关不会放宽字段白名单，也不接受字符串布尔值或伪造身份。"""
        for value in ('false', 0, None, []):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, 'validate_only must be a boolean'):
                    resolve_request({'validate_only': value}, self.root)
                self.request.write_text(json.dumps({**self.body, 'validate_only': value}), encoding='utf-8')
                with self.assertRaisesRegex(ValueError, 'validate_only must be a boolean'):
                    resolve_request({'request_path': str(self.request), 'validate_only': True}, self.root)
        for key in ('_dsh_context', 'request_path', 'unexpected'):
            self.request.write_text(json.dumps({**self.body, key: {}}), encoding='utf-8')
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'unsupported fields'):
                resolve_request({'request_path': str(self.request)}, self.root)
        self.request.write_text(json.dumps(self.body), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'not both'):
            resolve_request({'request_path': str(self.request), 'objective': 'conflict'}, self.root)

    def test_file_validation_does_not_acquire_a_save_lock_or_publish_a_plan(self):
        """实际规划入口识别文件内校验开关，返回缺项而不保存计划或开启评审。"""
        directory = self.root / 'operations' / 'validation'
        with (patch('pipelines.benchmark.design.planner.exclusive_run') as lock,
              patch('pipelines.benchmark.design.planner.current', return_value=None),
              patch('pipelines.benchmark.design.planner.activate') as activate,
              patch('pipelines.benchmark.design.planner.gate') as gate):
            result = plan({'request_path': str(self.request), '_dsh_context': {'session_id': 'session'}}, directory)
        self.assertEqual(result['state'], 'needs_plan_revision')
        self.assertIn('Provide assessment_path', result['issues'][0])
        lock.assert_not_called()
        activate.assert_not_called()
        gate.assert_not_called()
        self.assertFalse((directory / 'plan.json').exists())

    def test_explicit_save_reads_the_file_once_and_uses_the_conversation_lock(self):
        """保存只消费一次解析快照，并保留会话锁与输入来源。"""
        with (patch('pipelines.benchmark.design.planner.resolve_request', wraps=resolve_request) as resolve,
              patch('pipelines.benchmark.design.planner.exclusive_run') as lock,
              patch('pipelines.benchmark.design.planner._plan', return_value={'state': 'planned'}) as submit):
            result = plan({'request_path': str(self.request), 'validate_only': False,
                           '_dsh_context': {'session_id': 'session'}}, self.root / 'operations' / 'save')
        self.assertEqual(result['state'], 'planned')
        resolve.assert_called_once()
        lock.assert_called_once()
        self.assertIs(submit.call_args.args[0]['validate_only'], False)
        self.assertEqual(submit.call_args.args[3], {'request_path': str(self.request.resolve())})
