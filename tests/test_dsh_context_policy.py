"""Compaction configuration must fit the selected model without reducing its capabilities."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from dsh.presets import build_preset as base
from dsh.presets.build_presets import build
from dsh.presets.context_policy import configure_context_policy
from dsh.presets.profile_migration import migrate_profile


# 构造具有原生压缩分组的标准预设，验证真实生成与迁移入口。
def standard():
    return [{'insert': [{'id': 'preset-standard', 'name': '@deepseek-ai/dsh-agent-preset',
                         'config': {'id': 'standard', 'plugins': [
                             {'id': 'persona', 'config': {}},
                             {'id': 'compaction', 'name': 'cordis:group', 'config': [
                                 {'id': 'compaction-basic', 'name': '@deepseek-ai/dsh-compaction-basic'},
                             ]},
                         ]}}]}]


# 取得自有预设中的压缩配置，断言只修改这一领域的参数。
def compaction(profile):
    preset = next(row for patch in profile for row in patch.get('insert', [])
                  if row.get('config', {}).get('id') == 'benchforge')
    group = next(row for row in preset['config']['plugins'] if row['id'] == 'compaction')
    return group['config'][0].setdefault('config', {})


class ContextPolicyTests(unittest.TestCase):
    # 保存不含凭据的模型注册表，使用生产代码的 DSH 角色解析路径。
    def config(self, root, window=262144, output=131072):
        file = root / 'config.json'
        file.write_text(json.dumps({'providers': {'FastQwen': {
            'model': 'test-model', 'url': 'http://127.0.0.1:18041/v1',
            'context_window': window, 'max_tokens': output, 'reasoning_effort': 'xhigh',
        }}}), encoding='utf-8')
        return file

    # 验证当前 256K 模型只调整摘要预算，主回答上限与推理强度保持完整。
    def test_large_model_generation_reserves_summary_budget_separately(self):
        with tempfile.TemporaryDirectory() as directory:
            config = self.config(Path(directory))
            source = standard()
            generated = build(source, 'python3', config, config)
            self.assertEqual(compaction(generated)['modelPolicies'], [{
                'provider': 'benchforge-models', 'model': 'test-model',
                'headroomTokens': 32768, 'maxTokens': 16384,
            }])
            patches = {row['id']: row for row in generated if 'insert' not in row}
            model = patches['llm-pi-ai']['config']['providers']['benchforge-models']['models'][0]
            self.assertEqual((model['contextWindow'], model['maxTokens']), (262144, 131072))
            self.assertEqual(patches['agent-default-model']['config']['reasoningEffort'], 'xhigh')
            self.assertEqual(source, standard())

    # 验证小窗口按可用预算缩放，不把 16K 安全余量生硬套到小模型上。
    def test_small_model_policy_scales_below_its_available_window(self):
        with tempfile.TemporaryDirectory() as directory:
            config = self.config(Path(directory), window=8192, output=1024)
            policy = compaction(build(standard(), 'python3', config, config))['modelPolicies'][0]
            self.assertEqual(policy['headroomTokens'], 1792)
            self.assertLessEqual(policy['maxTokens'], 1024)
            self.assertGreater(8192 - 1024 - policy['headroomTokens'], (8192 - 1024) * .16)

    # 验证便携包不编造模型容量，部署读取注册表后能幂等补齐同一路由策略。
    def test_portable_profile_gets_policy_when_model_configuration_is_available(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.config(root)
            portable = build(standard(), 'python3', config, config, package_imports=True)
            self.assertNotIn('modelPolicies', compaction(portable))
            profile = root / 'profile.yml'
            profile.write_text(base.yaml.dump(portable, Dumper=base.Dumper), encoding='utf-8')
            base.refresh_model_patches(profile, config)
            first = profile.read_text(encoding='utf-8')
            base.refresh_model_patches(profile, config)
            self.assertEqual(profile.read_text(encoding='utf-8'), first)
            loaded = base.yaml.load(first, Loader=base.Loader)
            self.assertEqual(len(compaction(loaded)['modelPolicies']), 1)
            self.assertEqual(compaction(loaded)['modelPolicies'][0]['headroomTokens'], 32768)

    # 验证重新生成与安装都保留操作者的显式压缩选项及其他模型策略。
    def test_profile_migration_preserves_explicit_compaction_overrides(self):
        with tempfile.TemporaryDirectory() as directory:
            config = self.config(Path(directory))
            generated = build(standard(), 'python3', config, config)
            prior = deepcopy(generated)
            compaction(prior).update(auto=False, headroomTokens=8192, modelPolicies=[
                {'provider': 'benchforge-models', 'model': 'test-model', 'maxTokens': 4096},
                {'provider': 'other', 'model': 'other-model', 'headroomTokens': 1000},
            ])
            merged = migrate_profile(prior, generated)
            options = compaction(merged)
            self.assertFalse(options['auto'])
            self.assertEqual(options['modelPolicies'][0], {
                'provider': 'benchforge-models', 'model': 'test-model', 'maxTokens': 4096, 'headroomTokens': 8192,
            })
            self.assertEqual(options['modelPolicies'][1], compaction(prior)['modelPolicies'][1])
            self.assertEqual(compaction(generated)['modelPolicies'][0]['headroomTokens'], 32768)

    # 验证不明容量不会被猜测，且非 BenchForge 预设不会接收领域配置。
    def test_unknown_capacities_and_unrelated_presets_are_not_changed(self):
        with tempfile.TemporaryDirectory() as directory:
            config = self.config(Path(directory))
            patches = base.model_patches(config)
            unrelated = standard()
            before = deepcopy(unrelated)
            configure_context_policy(unrelated, patches)
            self.assertEqual(unrelated, before)
            generated = build(standard(), 'python3', config, config, package_imports=True)
            patches[0]['config']['providers']['benchforge-models']['models'][0].pop('contextWindow')
            configure_context_policy(generated, patches)
            self.assertNotIn('modelPolicies', compaction(generated))


if __name__ == '__main__':
    unittest.main()
