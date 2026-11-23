"""Persona composition and installation must work without deployment workarounds."""
from copy import deepcopy
from pathlib import Path
import re
import tempfile
import unittest

from dsh.presets import build_preset as base
from dsh.presets.build_presets import build
from dsh.presets.profile_migration import insert_host_dependencies, migrate_profile
from capabilities.benchmark_design.assessment import assessment_template
from service_access.tool_names import NAMES


# 构造最小官方标准预设夹具，使生成器测试不依赖完整第三方运行时。
def standard():
    return [{'insert': [{'id': 'preset-standard', 'name': '@deepseek-ai/dsh-agent-preset',
                        'config': {'id': 'standard', 'plugins': [
                            {'id': 'persona', 'name': '@deepseek-ai/dsh-persona',
                             'config': {'prefix': 'Model {{model}}'}},
                        ]}}]}]


# 从生成 profile 中定位 BenchForge 预设的插件列表。
def plugins(profile):
    return next(row['config']['plugins'] for patch in profile for row in patch.get('insert', [])
                if row.get('id') == 'preset-benchforge')


# 取得生成预设的人设配置，供提示模板和迁移断言使用。
def persona(profile):
    return next(row['config'] for row in plugins(profile) if row['id'] == 'persona')


class DshPresetTests(unittest.TestCase):
    # 验证组件指南在 profile 合并后仍唯一存在，且只作用于自有预设。
    def test_component_skills_survive_profile_merge_and_remain_preset_scoped(self):
        for portable in (False, True):
            generated = self.generated(portable)
            merged = migrate_profile(generated, generated)
            rows = [row for row in plugins(merged) if row['id'] == 'benchforge-skill-files']
            self.assertEqual(len(rows), 1)
            if portable:
                self.assertEqual(rows[0]['name'], '@eurecamoment/benchforge-dsh/component-skills')
            else:
                self.assertTrue(rows[0]['name'].endswith('/dist/component-skills.mjs'))
            host_rows = [row for patch in merged for row in patch.get('insert', [])]
            self.assertFalse(any(row.get('id') == 'benchforge-skill-files' for row in host_rows))
            self.assertEqual(len([row for row in host_rows if row.get('id') == 'benchforge-access']), 1)

    # 验证宿主评审定时器与工作台读取使用同一服务数据库配置。
    def test_host_review_timer_uses_the_workbench_database_configuration(self):
        for portable in (False, True):
            generated = self.generated(portable)
            entries = {row['id']: row for patch in generated for row in patch.get('insert', [])}
            self.assertEqual(entries[base.SESSION_OPERATIONS_PLUGIN_ID]['config']['productionConfig'],
                             entries[base.WORKBENCH_PLUGIN_ID]['config']['productionConfig'])

    # 以临时且无模型凭据的配置构建本机或便携预设夹具。
    def generated(self, portable=False):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'missing-config.json'
            return build(standard(), 'python3', config, config, package_imports=portable)

    # 按 DSH 内置变量集合检查提示模板，拒绝引用不存在的模板变量。
    def assert_renderable(self, profile):
        values = {'provider': 'test-provider', 'model': 'test-model'}
        for field in ('prefix', 'suffix'):
            template = persona(profile).get(field, '')
            # Match DSH's strict default-variable contract: unknown names fail.
            rendered = re.sub(r'{{\s*([a-z][a-z0-9_]*)\s*}}',
                              lambda match: values[match[1]], template)
            self.assertNotRegex(rendered, r'{{[^{}]+}}')

    # 验证本机和便携生成预设均只依赖内置提示变量。
    def test_local_and_portable_presets_need_only_builtin_prompt_variables(self):
        for portable in (False, True):
            with self.subTest(portable=portable):
                self.assert_renderable(self.generated(portable))

    # 验证已发布的随包预设提示同样能用内置变量渲染。
    def test_published_bundle_needs_only_builtin_prompt_variables(self):
        file = Path(__file__).parents[1] / 'src/dsh/cordis.patch.yml'
        self.assert_renderable(base.yaml.load(file.read_text(encoding='utf-8'), Loader=base.Loader))

    # 验证升级修正过时的自动后缀，同时保留本地部署设置。
    def test_upgrade_repairs_generated_suffix_and_preserves_local_settings(self):
        generated = self.generated()
        old = deepcopy(generated)
        persona(old)['suffix'] = 'Your working directory is {{cwd}}. You are using {{model}}.'
        extra = {'id': 'local-grpc', 'name': 'file:///instance/grpc.mjs', 'config': {'port': 50051}}
        plugins(old).append(extra)
        limits = {'id': 'attachment-local', 'config': {'maxImages': 1000}}
        old.extend([limits, {'id': 'hmr', 'disabled': True}])
        updated = migrate_profile(old, generated)
        self.assert_renderable(updated)
        self.assertIn(extra, plugins(updated))
        self.assertIn(limits, updated)
        self.assertIn({'id': 'hmr', 'disabled': True}, updated)

    # 验证操作者自定义的人设后缀不会被生成器升级覆盖。
    def test_upgrade_preserves_operator_persona_suffix(self):
        generated = self.generated()
        old = deepcopy(generated)
        custom = 'Deployment {{custom_context}}; use {{model}}.'
        persona(old)['suffix'] = custom
        self.assertEqual(persona(migrate_profile(old, generated))['suffix'], custom)

    # 验证工具引用策略只安装一次，重复合并后依然有效。
    def test_tool_reference_policy_is_installed_once_and_survives_merge(self):
        for portable in (False, True):
            generated = self.generated(portable)
            merged = migrate_profile(generated, generated)
            entries = [row for row in plugins(merged) if row['id'] == 'benchforge-invocation']
            self.assertEqual(len(entries), 1)
            self.assertTrue(entries[0]['name'].endswith('/invocation' if portable else '/invocation.mjs'))
            reviews = [row for row in plugins(merged) if row['id'] == 'benchforge-review-policy']
            self.assertEqual(len(reviews), 1)
            self.assertIn('S2 场景选取或新建 → S3 采集', persona(merged)['prefix'])

    # 验证评估模板说明引用实际登记的规范工具名称。
    def test_assessment_guidance_uses_registered_tool_names(self):
        template = assessment_template()
        self.assertIn(NAMES['pipeline_catalog'], template['instructions'])
        self.assertIn(NAMES['pipeline_plan'], template['scene_binding'])
        for field in ('instructions', 'scene_binding'):
            self.assertNotRegex(template[field], r'\b(' + '|'.join(NAMES) + r')\b')

    # 验证全新和已有 profile 都只装配一个随包示例导入入口。
    def test_example_import_is_installed_once_for_fresh_and_existing_profiles(self):
        generated = self.generated()
        for profile in (generated, [{'insert': [row for patch in generated for row in patch.get('insert', [])
                                               if row.get('id') != base.EXAMPLES_PLUGIN_ID]}]):
            insert_host_dependencies(profile)
            insert_host_dependencies(profile)
            rows = [row for patch in profile for row in patch.get('insert', [])
                    if row.get('id') == base.EXAMPLES_PLUGIN_ID]
            self.assertEqual(len(rows), 1)
            self.assertTrue(Path(rows[0]['config']['sourceDirectory']).is_absolute())
        disabled = self.generated()
        row = next(row for patch in disabled for row in patch.get('insert', [])
                   if row.get('id') == base.EXAMPLES_PLUGIN_ID)
        row['config']['enabled'] = False
        row['config']['sourceDirectory'] = '/custom/example'
        merged = migrate_profile(disabled, self.generated())
        example = next(row for patch in merged for row in patch.get('insert', [])
                       if row.get('id') == base.EXAMPLES_PLUGIN_ID)
        self.assertFalse(example['config']['enabled'])
        self.assertEqual(example['config']['sourceDirectory'], '/custom/example')

    # 验证便携包的示例来源显式由公开包位置解析。
    def test_package_example_source_is_explicit_and_portable(self):
        row = next(row for patch in self.generated(True) for row in patch.get('insert', [])
                   if row.get('id') == base.EXAMPLES_PLUGIN_ID)
        self.assertIn('dist/example/SpatialForge', row['config']['sourceDirectory'])
        self.assertEqual(row['name'], '@eurecamoment/benchforge-dsh/examples')

    # 验证随包预设的人设和工具调用配置与生成器一致。
    def test_portable_persona_and_invocation_match_the_generator(self):
        file = Path(__file__).parents[1] / 'src/dsh/cordis.patch.yml'
        published = base.yaml.load(file.read_text(encoding='utf-8'), Loader=base.Loader)
        self.assertEqual(persona(published), persona(self.generated(True)))
        self.assertEqual(len([row for row in plugins(published) if row['id'] == 'benchforge-invocation']), 1)

    # 确保活动人设与 FastTRELLIS 技能准确描述资产自动登记和适用对象偏好。
    def test_trellis_guidance_prefers_suitable_objects_and_uses_registered_asset_id(self):
        profile = self.generated(True)
        prefix = persona(profile)['prefix']
        self.assertIn('优先使用 TRELLIS', prefix)
        self.assertIn('直接使用其已登记 asset_id', prefix)
        self.assertIn('不合适的对象硬塞进 TRELLIS', prefix)
        self.assertIn('do not send that same output through benchforge_mesh_import again', prefix)

        skill = Path(__file__).parents[1] / 'src/dsh/skills/fast-trellis/SKILL.md'
        text = skill.read_text(encoding='utf-8')
        self.assertIn('该工具会登记生成的网格并返回 `asset_id`', text)
        self.assertIn('不要再次把同一个 TRELLIS 输出交给 `benchforge_mesh_import`', text)


if __name__ == '__main__':
    unittest.main()
