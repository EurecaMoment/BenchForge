import unittest

from capabilities.isaac_sim.appearance_evidence import appearance_evidence, observe_render_sources


class CaptureAppearanceEvidenceTests(unittest.TestCase):
    # 同一纹理文件可用于不同物体，回执必须保留各物体的绑定路径及上传摘要。
    def test_uploaded_material_and_rendered_light(self):
        texture = {'relative_path': 'textures/base.png', 'sha256': '0123', 'size_bytes': 64}
        mesh = '/World/Scene/Objects/chair/Part0'
        material = '/World/Scene/Materials/chair_Part0'
        lamp = '/World/Scene/Lights/Light0'
        compiled = {'mesh_assets': {'chair': {'entity_id': 'chair', 'asset_id': 'registered',
            'vertices': 6, 'transform': {'shape_preserved': True},
            'bindings': [{'prim_path': mesh, 'material_prim': material, 'material_index': 0,
                          'textures': {'base_color': texture['relative_path']}}]}},
            'light_inputs': [{'prim_path': lamp, 'kind': 'DomeLight', 'authored': {'intensity': 1}}]}
        manifest = {'asset_id': 'uploaded', 'relative_path': 'scene.usda', 'sha256': '4567',
                    'files': [texture]}
        prims = [{'path': mesh, 'type': 'Mesh'}, {'path': material, 'type': 'Material'},
                 {'path': lamp, 'type': 'DomeLight'}]
        sources = {}
        observe_render_sources(sources, {'usd_sources': [{'prim': mesh, 'kind': 'mesh'},
            {'prim': lamp, 'kind': 'DomeLight', 'radiance': [1, 1, 1]}]})
        evidence = appearance_evidence(compiled, manifest, prims, sources)
        asset = evidence['asset_dependencies'][0]
        self.assertEqual(asset['texture_slots'], ['base_color'])
        self.assertEqual(asset['uploaded_scene']['asset_id'], 'uploaded')
        appearance = evidence['material_dependencies'][0]
        self.assertTrue(appearance['material_inspected'] and appearance['renderer_reported_mesh'])
        self.assertEqual(appearance['textures'], [{'slot': 'base_color', **texture}])
        self.assertEqual(evidence['light_receipts'][0]['renderer_radiance'], [1, 1, 1])
        with self.assertRaisesRegex(ValueError, 'absent from frozen'):
            appearance_evidence(compiled, {**manifest, 'files': []}, prims, sources)

    # 原生检查能确认灯光节点；渲染源缺失时不能声称辐射值已经执行。
    def test_authored_light_without_renderer_evidence(self):
        lamp = {'prim_path': '/World/Scene/Lights/Light0', 'kind': 'RectLight',
                'authored': {'intensity': 8}}
        prims = [{'path': lamp['prim_path'], 'type': 'RectLight'}]
        receipt = appearance_evidence({'light_inputs': [lamp]}, {}, prims, {})['light_receipts'][0]
        self.assertTrue(receipt['prim_inspected'])
        self.assertFalse(receipt['renderer_reported'])
        self.assertNotIn('renderer_radiance', receipt)
        self.assertEqual(receipt['authored']['intensity'], 8)

    # 旧编译清单或无灯无贴图的模拟回执无需伪造外观证据。
    def test_missing_optional_metadata(self):
        sources = {}
        observe_render_sources(sources, {})
        self.assertEqual(appearance_evidence({}, {}, [], sources),
                         {'asset_dependencies': [], 'material_dependencies': [], 'light_receipts': []})
