"""Plan admission and scene admission share declaration and reference contracts."""
from unittest import TestCase

from capabilities.scene_authoring.contracts.scene_design import saved_design, validate_geometry_declarations


class ScenePlanContractTests(TestCase):
    # 验证错放的例外声明在 S1 就被识别，而非等到场景生产时当作未申报。
    def test_misplaced_declaration_is_not_silently_approved(self):
        scenes = {'designs': [{'id': 'villa', 'geometry_exceptions': [{'object_ids': ['wall']}]}]}
        with self.assertRaisesRegex(ValueError, r'scenes.designs\[0\].geometry_exceptions is misplaced'):
            validate_geometry_declarations(scenes)

    # 验证合法顶层声明仍兼容历史副本的设计内元数据，不破坏已保存的生产计划。
    def test_canonical_declaration_is_authoritative_over_old_nested_metadata(self):
        declaration = dict(scene_design_id='villa', scene_id='villa', object_ids=['wall'],
                           purpose='Navigation test', reason='Architectural shell',
                           simplifications='Composite walls', retained_constraints='Physical wall clearance',
                           excluded_claims='Photorealistic interior')
        scenes = {'designs': [{'id': 'villa', 'geometry_exceptions': [{'object_ids': ['wall']}]}],
                  'geometry_exceptions': [declaration]}
        self.assertEqual(validate_geometry_declarations(scenes), [declaration])

    # 验证引用错误给出准确字段及局部修复位置，避免误导模型重建已审核计划。
    def test_reference_error_points_to_program_field_not_plan_recreation(self):
        program = {'provenance': {'sources': [{'kind': 'scene_plan', 'plan': '/saved/plan.json'}]}}
        with self.assertRaisesRegex(ValueError, 'field "path".*do not recreate the plan'):
            saved_design(program)
