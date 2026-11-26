"""Discovery distinguishes declared assets from executable bindings."""

from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from capabilities.scene_authoring.catalog.operators import catalog_response
from capabilities.asset_generation.generation_stage import validate_generation_request
from capabilities.asset_generation.generation import GenerationTools
from capabilities.asset_generation.mesh_input import validate_mesh_input


class AssetDiscoveryTests(unittest.TestCase):
    # 验证内建原生资产在 v2 明确标为不支持，并指向 mesh 离线转换路径。
    def test_catalog_marks_builtin_native_assets_unsupported(self):
        request = {'section': 'native_assets', 'item_id': 'mug'}
        store = SimpleNamespace(root=Path('.'))
        for settings in ({}, None):
            with self.subTest(settings=settings):
                if settings is None:
                    value = catalog_response(store, request)['items'][0]['value']
                else:
                    value = catalog_response(store, request, settings=settings)['items'][0]['value']
                self.assertEqual(value['availability'], 'unsupported')
                self.assertIn('mesh_assets', value['next_action'])
                self.assertIn('kind="mesh"', value['next_action'])
                self.assertNotIn('path', value)
                self.assertNotIn('asset_sources', value['availability_basis'])

    # 验证静态网格导入在快照和转换前拒绝可能丢失关节语义的 USD 输入。
    def test_usd_rejected_before_input_snapshot_or_conversion(self):
        tools = Mock()
        with self.assertRaisesRegex(ValueError, 'does not preserve articulation'):
            validate_generation_request(tools, 'mesh_import', {'source_mesh': 'drone.usdc', 'label': 'drone'})
        tools.source_path.assert_not_called()
        with patch.object(GenerationTools, '_publish', side_effect=AssertionError('conversion started')):
            with self.assertRaisesRegex(ValueError, 'native_asset'):
                GenerationTools.import_asset(tools, 'drone.usd', {}, '.', Mock())

    # 验证静态网格支持格式大小写兼容，并明确拒绝原生容器和未知扩展名。
    def test_static_formats_have_explicit_support(self):
        for suffix in ('GLB', 'gltf', 'obj', 'ply', 'stl'):
            validate_mesh_input('asset.' + suffix)
        for suffix in ('usdz', 'xml', 'urdf', 'zip', 'unknown'):
            with self.subTest(suffix=suffix), self.assertRaisesRegex(ValueError, 'static meshes'):
                validate_mesh_input('asset.' + suffix)
