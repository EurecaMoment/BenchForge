"""从工具参数解析到上传校验的回归：本地来源与资产内部文件名不可混用。"""

import importlib.util
import os
from pathlib import Path
from shutil import copyfile
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from capabilities.isaac_sim.tool_api import execute
from capabilities.isaac_sim.upload_access import validate_upload
from database.access import AccessScope
from service_access.access_scope import TrustedAccess, access_context
from service_access.input_policy import resolve_arguments


class IsaacUploadInputTests(unittest.TestCase):
    def setUp(self):
        """准备隔离的会话目录和有效 STL 输入，不连接实际仿真服务。"""
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name).resolve()
        self.source = self.workspace / 'tasks/drone_villa/work/drone/rotor.stl'
        self.source.parent.mkdir(parents=True)
        self.source.write_bytes(b'rotor'.ljust(80, b'\0') + b'\0' * 4)
        self.directory = self.workspace / 'operations/upload-test'
        self.directory.mkdir(parents=True)
        self.access = TrustedAccess(AccessScope('host', 'session', 'workspace', 'owner', 1, 1),
                                    str(self.workspace))
        self.request = {'action': 'upload', 'asset_id': 'owned-asset',
                        'relative_path': 'meshes/rotor.stl',
                        'source_path': 'tasks/drone_villa/work/drone/rotor.stl'}

    def test_resolved_upload_name_reaches_asset_validation_unchanged(self):
        """复现原始参数组合，确认补全来源路径后仍可通过资产文件名校验。"""
        with access_context(self.access):
            request = resolve_arguments(self.request, self.workspace)
            validate_upload(request['source_path'], request['relative_path'])
        self.assertEqual(request['relative_path'], 'meshes/rotor.stl')

    def test_asset_path_traversal_remains_rejected_by_domain_validation(self):
        """免于本地路径转换不代表放行越界、绝对路径或编码后的资源名。"""
        for name in ('../rotor.stl', '/tmp/rotor.stl', 'C:rotor.stl',
                     'meshes/../../rotor.stl', '..%2Frotor.stl', 'meshes\\rotor.stl'):
            with self.subTest(name=name), access_context(self.access):
                request = resolve_arguments({**self.request, 'relative_path': name}, self.workspace)
                self.assertEqual(request['relative_path'], name)
                with self.assertRaises(PermissionError):
                    validate_upload(request['source_path'], request['relative_path'])

    @unittest.skipUnless(importlib.util.find_spec('pxr'), 'USD parser is not installed')
    def test_upload_returns_display_color_warning_and_sends_original_bytes(self):
        source = self.source.with_name('room.usda')
        original = b'#usda 1.0\ndef Cube "Wall" {\n color3f primvars:displayColor = (1, 0, 0)\n}\n'
        source.write_bytes(original)
        client = Mock(endpoint='simulator:50051')

        def upload(path, *, asset_id, relative_path):
            self.assertEqual(path.read_bytes(), original)
            return {'asset_id': asset_id, 'relative_path': relative_path}

        client.upload.side_effect = upload
        request = {**self.request, 'source_path': str(source), 'relative_path': 'rooms/room.usda'}
        with (access_context(self.access),
                patch('capabilities.isaac_sim.tool_api.A100Client', return_value=client),
                patch('capabilities.isaac_sim.tool_api.NativeResources', return_value=Mock(access=self.access)),
                patch('capabilities.isaac_sim.upload_access.copy_input',
                      side_effect=lambda root, path, target, *, limit: copyfile(path, target))):
            result = execute(resolve_arguments(request, self.workspace), self.directory, {})
        client.upload.assert_called_once()
        self.assertEqual(result['asset'], {'asset_id': 'owned-asset', 'relative_path': 'rooms/room.usda'})
        warning, = result['warnings']
        self.assertEqual(warning['code'], 'usd_display_color_scalar')
        self.assertEqual(warning['layer'], 'rooms/room.usda')
        self.assertEqual(warning['path'], '/Wall.primvars:displayColor')
        self.assertEqual(source.read_bytes(), original)

    @unittest.skipUnless(os.name == 'posix', 'Private upload snapshots require Linux descriptors')
    def test_scoped_upload_transmits_snapshot_and_preserves_asset_identity(self):
        """跑通真实参数解析、私有快照和上传分派，只替换网络及所有权存储。"""
        client = Mock(endpoint='simulator:50051')
        resources = Mock(access=self.access)
        snapshots = []

        def upload(path, *, asset_id, relative_path):
            """记录发送边界的快照与元数据，确保未传入原文件或绝对目标路径。"""
            snapshots.append(path)
            self.assertFalse(path.is_relative_to(self.workspace))
            self.assertEqual(path.read_bytes(), self.source.read_bytes())
            self.assertEqual(asset_id, 'owned-asset')
            self.assertEqual(relative_path, 'meshes/rotor.stl')
            return {'asset_id': asset_id, 'relative_path': relative_path}

        client.upload.side_effect = upload
        with access_context(self.access), \
                patch('capabilities.isaac_sim.tool_api.A100Client', return_value=client), \
                patch('capabilities.isaac_sim.tool_api.NativeResources', return_value=resources):
            request = resolve_arguments(self.request, self.workspace)
            result = execute(request, self.directory, {})
        self.assertEqual(result['asset']['relative_path'], 'meshes/rotor.stl')
        resources.require.assert_called_once_with('asset', 'owned-asset')
        resources.remember.assert_called_once_with('asset', 'owned-asset', origin='upload')
        client.close.assert_called_once_with()
        self.assertFalse(snapshots[0].exists())

    @unittest.skipUnless(os.name == 'posix', 'Private upload snapshots require Linux descriptors')
    def test_invalid_asset_member_is_rejected_before_network_upload(self):
        """跨资产的目标文件名必须在网络发送之前被拒绝。"""
        client = Mock(endpoint='simulator:50051')
        with access_context(self.access), \
                patch('capabilities.isaac_sim.tool_api.A100Client', return_value=client), \
                patch('capabilities.isaac_sim.tool_api.NativeResources', return_value=Mock(access=self.access)):
            request = resolve_arguments({**self.request, 'relative_path': '../rotor.stl'}, self.workspace)
            with self.assertRaises(PermissionError):
                execute(request, self.directory, {})
        client.upload.assert_not_called()
        client.close.assert_called_once_with()
