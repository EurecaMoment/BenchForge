"""Regression coverage for A100 render limits and effective capture settings."""
import json
from pathlib import Path
import unittest

from capabilities.isaac_sim.authoring import compile_scene
from capabilities.isaac_sim.capture_stage import _capture_render_arguments
from capabilities.scene_authoring.catalog.capabilities import scene_contract_capabilities
from capabilities.scene_authoring.validation.program import validate_program
from service_access.configuration import validate_providers
from test_scene_diagnostics import sample


class RenderLimitTests(unittest.TestCase):
    def test_runtime_configuration_accepts_new_limits_and_rejects_values_above_them(self):
        validate_providers({'providers': {'IsaacSim4A100': {
            'image_size': 2048, 'samples_per_pixel': 1024}}})
        for field, value in (('image_size', 2049), ('samples_per_pixel', 1025)):
            with self.subTest(field=field):
                with self.assertRaisesRegex(ValueError, f'{field} must be an integer'):
                    validate_providers({'providers': {'IsaacSim4A100': {field: value}}})

    def test_scene_program_contract_and_capability_declaration_match_new_limits(self):
        program = sample()
        program['render_environment'] = {'samples_per_pixel': 1024}
        validate_program(program)
        program['render_environment']['samples_per_pixel'] = 1025
        with self.assertRaisesRegex(ValueError, 'samples_per_pixel must be 16..1024'):
            validate_program(program)
        controls = scene_contract_capabilities()['render_controls']
        self.assertEqual(controls['samples_per_pixel'], [1, 1024])
        self.assertEqual(controls['image_size'], [64, 2048])

    def test_configured_samples_are_a_floor_and_reach_capture_arguments(self):
        program = sample()
        program['render_environment'] = {'samples_per_pixel': 96}
        settings = {'image_size': 2048, 'samples_per_pixel': 256}
        compiled = compile_scene(program, Path('.'), settings)
        self.assertEqual(compiled['samples_per_pixel'], 256)
        self.assertEqual(_capture_render_arguments(settings, compiled), {
            'size': 2048, 'spp': 256, 'annotations': True})

        program['render_environment']['samples_per_pixel'] = 512
        compiled = compile_scene(program, Path('.'), settings)
        self.assertEqual(compiled['samples_per_pixel'], 512)

        settings['samples_per_pixel'] = 768
        compiled = compile_scene(program, Path('.'), settings)
        self.assertEqual(compiled['samples_per_pixel'], 768)
        self.assertEqual(_capture_render_arguments(settings, compiled)['spp'], 768)

        settings['samples_per_pixel'] = 1024
        compiled = compile_scene(program, Path('.'), settings)
        self.assertEqual(compiled['samples_per_pixel'], 1024)
        self.assertEqual(_capture_render_arguments(settings, compiled), {
            'size': 2048, 'spp': 1024, 'annotations': True})

    def test_render_defaults_are_1024_pixels_and_256_samples(self):
        example = json.loads(Path('config/benchforge.example.json').read_text(encoding='utf-8'))
        settings = example['providers']['IsaacSim4A100']
        self.assertEqual(settings['image_size'], 1024)
        self.assertEqual(settings['samples_per_pixel'], 256)
        program = sample()
        compiled = compile_scene(program, Path('.'), settings)
        self.assertEqual(compiled['samples_per_pixel'], 256)
        self.assertEqual(_capture_render_arguments(settings, compiled), {
            'size': 1024, 'spp': 256, 'annotations': True})


if __name__ == '__main__':
    unittest.main()
