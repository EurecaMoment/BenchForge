"""Native render evidence must report the backend that actually produced each frame."""
import unittest

from capabilities.isaac_sim.evidence import common_render_metadata, render_metadata


class CaptureRenderMetadataTests(unittest.TestCase):
    def test_frame_backend_overrides_physics_service_cpu_flag(self):
        observed = render_metadata(
            {'renderer': 'Mitsuba CPU scalar path tracer', 'render_backend': 'scalar_rgb',
             'cpu_fallback': True, 'native_rtx': False, 'spp': 256, 'resolution': [768, 768]},
            {'cpu_fallback': False, 'renderer': 'other'})
        self.assertEqual(observed['renderer'], 'Mitsuba CPU scalar path tracer')
        self.assertEqual(observed['render_backend'], 'scalar_rgb')
        self.assertTrue(observed['cpu_fallback'])
        self.assertFalse(observed['native_rtx'])
        self.assertEqual(observed['samples_per_pixel'], 256)
        self.assertEqual(observed['resolution'], [768, 768])

    def test_batched_capture_renderer_is_retained_without_inventing_defaults(self):
        observed = render_metadata({'spp': 64}, {'renderer': 'Mitsuba LLVM CPU path tracer',
                                   'render_backend': 'llvm_ad_rgb', 'cpu_fallback': True})
        self.assertEqual(observed['renderer'], 'Mitsuba LLVM CPU path tracer')
        self.assertNotIn('native_rtx', observed)
        self.assertEqual(render_metadata({}), {})
        self.assertEqual(common_render_metadata([]), {})

    def test_aggregate_never_labels_mixed_frames_as_a_single_backend(self):
        frames = [{'render_settings': {'renderer': 'CPU', 'cpu_fallback': True,
                                      'samples_per_pixel': 64}},
                  {'render_settings': {'renderer': 'CUDA', 'cpu_fallback': False,
                                      'samples_per_pixel': 64}}]
        observed = common_render_metadata(frames)
        self.assertNotIn('renderer', observed)
        self.assertNotIn('cpu_fallback', observed)
        self.assertEqual(observed['samples_per_pixel'], 64)
        self.assertTrue(observed['varies_by_frame'])


if __name__ == '__main__':
    unittest.main()
