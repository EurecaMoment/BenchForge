"""Discovery distinguishes declared assets from executable bindings."""

from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from capabilities.scene_authoring.catalog.operators import catalog_response
from capabilities.asset_generation.generation_stage import validate_generation_request
from capabilities.asset_generation.generation import GenerationTools
from capabilities.asset_generation.mesh_input import validate_mesh_input
from pipelines.scene_production.planning import SCENE_ASSET_ROUTE_GUIDANCE


class AssetDiscoveryTests(unittest.TestCase):
    # 确保场景规划把 TRELLIS 作为合适独立物件的软默认路线，并保留其他表示方式。
    def test_scene_planner_prefers_trellis_without_forcing_every_object(self):
        self.assertIn('existing registered asset when it semantically matches', SCENE_ASSET_ROUTE_GUIDANCE)
        self.assertIn('suitable independent recognizable object', SCENE_ASSET_ROUTE_GUIDANCE)
        self.assertIn('diffusion→SAM3→TRELLIS', SCENE_ASSET_ROUTE_GUIDANCE)
        self.assertIn('without quotas', SCENE_ASSET_ROUTE_GUIDANCE)
        self.assertIn('articulated or simulator-specific objects', SCENE_ASSET_ROUTE_GUIDANCE)

    # 确认目录描述与实现一致，TRELLIS 资产会自动登记并直接返回 SceneProgram 可用身份。
    def test_trellis_catalog_reports_registered_mesh_output(self):
        store = SimpleNamespace(root=Path('.'))
        result = catalog_response(store, {'section': 'generation_tools', 'item_id': 'trellis'})
        item = result['items'][0]['value']
        self.assertIn('registered', item['outputs'])
        self.assertIn('asset_id', item['outputs'])
        self.assertNotIn('mesh_import', item['outputs'])

    # 验证目录仅把部署已绑定的原生资源标为可用，避免将内置名称当成安装证明。
    def test_catalog_does_not_claim_unbound_assets_are_installed(self):
        request = {'section': 'native_assets', 'item_id': 'mug'}
        store = SimpleNamespace(root=Path('.'))
        missing = catalog_response(store, request, settings={})['items'][0]['value']
        self.assertEqual(missing['availability'], 'unconfigured')
        self.assertIn('kind=native', missing['next_action'])
        self.assertNotIn('path', missing)
        unknown = catalog_response(store, request)['items'][0]['value']
        self.assertEqual(unknown['availability'], 'unknown')
        bound = catalog_response(store, request, settings={'asset_sources': {
            'mug': {'asset_id': 'mug-pack', 'relative_path': 'mug.usdc'}}})['items'][0]['value']
        self.assertEqual(bound['availability'], 'configured')
        self.assertIn('require native evidence', bound['availability_basis'])

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
