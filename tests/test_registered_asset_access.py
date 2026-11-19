"""Asset authorization remains independent of the generation implementation."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from capabilities.asset_generation.asset_access import registered_asset_path


class RegisteredAssetAccessTests(unittest.TestCase):
    def test_missing_asset_query_has_no_directory_side_effect(self):
        """查询不存在资产只失败，不创建生成目录或启动生成器。"""
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaises(ValueError):
                registered_asset_path(root, 'asset_' + 'a' * 16)
            self.assertEqual(list(root.iterdir()), [])

    def test_scoped_queries_keep_shared_library_authorization(self):
        """查询必须透传可信身份及数据库，拒绝结果不能降级为本地路径。"""
        scope, engine = object(), object()
        with patch('capabilities.asset_generation.shared_library.asset_file',
                   side_effect=PermissionError('not granted')) as resolver:
            with self.assertRaises(PermissionError):
                registered_asset_path('.', 'asset_' + 'a' * 16, 'mesh.json', access=scope, engine=engine)
        resolver.assert_called_once_with(Path('.').resolve(), scope, 'asset_' + 'a' * 16,
                                         'mesh.json', engine=engine)

    def test_operator_lookup_retains_id_and_file_boundary_checks(self):
        """维护入口仅可读取登记资产内部文件，不能穿越至相邻目录。"""
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            folder = root / 'generated_assets' / ('asset_' + 'a' * 16)
            folder.mkdir(parents=True)
            source = folder / 'mesh.json'
            source.write_text('{}', encoding='utf-8')
            self.assertEqual(registered_asset_path(root, folder.name, 'mesh.json'), source.resolve())
            (folder.parent / 'outside.json').write_text('{}', encoding='utf-8')
            with self.assertRaises(ValueError):
                registered_asset_path(root, folder.name, '../outside.json')
