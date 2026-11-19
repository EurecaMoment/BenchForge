"""Jobs do not confer ownership of another conversation's simulator world."""

from unittest import TestCase
from unittest.mock import patch
from capabilities.isaac_sim.resource_access import NativeResources
from database.access import AccessScope
from service_access.access_scope import TrustedAccess, access_context


class NativeResourceAccessTests(TestCase):
    def test_uploaded_asset_features_do_not_require_a_retained_world(self):
        """资产导入及转换与原生提交遵循同一授权规则，不要求先创建世界。"""
        access = TrustedAccess(AccessScope('host', 'session', 'workspace', 'owner', 1, 1), '/tmp/workspace')
        cases = {'assets.import_robot': 'import_robot', 'assets.convert_glb': 'convert_asset',
                 'robot_types.robot_assets': 'import_robot'}
        with access_context(access), patch('capabilities.isaac_sim.resource_access.engine_for'):
            resources = NativeResources('127.0.0.1:50051')
            for feature, task in cases.items():
                request = {'asset_id': 'mine', 'relative_path': 'robot.urdf'}
                with self.subTest(feature=feature), patch.object(resources, 'require') as require:
                    resources.check({'feature_id': feature, 'arguments': request}, feature=True)
                    require.assert_called_once_with('asset', 'mine')
                    require.reset_mock()
                    resources.check({task: request})
                    require.assert_called_once_with('asset', 'mine')

    def test_asset_features_cannot_bypass_ownership_or_service_path_checks(self):
        """放行资产处理不能赋予其他会话的资产、世界或服务器文件权限。"""
        access = TrustedAccess(AccessScope('host', 'session', 'workspace', 'owner', 1, 1), '/tmp/workspace')
        with access_context(access), patch('capabilities.isaac_sim.resource_access.engine_for'):
            resources = NativeResources('127.0.0.1:50051')
            requests = ({'asset_id': 'other'}, {'source_path': '/private/robot.urdf'},
                        {'source': '/private/robot.urdf'}, {'session_id': 'other'})
            for feature in ('assets.import_robot', 'assets.convert_glb', 'robot_types.robot_assets'):
                for request in requests:
                    with (self.subTest(feature=feature, request=request),
                          patch.object(resources, 'owns', return_value=False),
                          self.assertRaises(PermissionError)):
                        resources.check({'feature_id': feature, 'arguments': request}, feature=True)
            with self.assertRaises(PermissionError):
                resources.check({'feature_id': 'scene.capture', 'arguments': {}}, feature=True)
            with self.assertRaises(PermissionError):
                resources.check({'extension': {'operation': 'assets.import_robot', 'arguments': {}}})

    # 验证跨会话世界被拒绝，自有世界中的 USD 场景节点路径可正常使用。
    def test_other_world_is_denied_and_owned_session_is_allowed(self):
        access = TrustedAccess(AccessScope('host','session','workspace','owner',1,1), '/tmp/workspace')
        with access_context(access), patch('capabilities.isaac_sim.resource_access.engine_for'), \
                patch('capabilities.isaac_sim.resource_access.read_document', return_value=None):
            resources = NativeResources('127.0.0.1:50051')
            with self.assertRaises(PermissionError):
                resources.check({'extension': {'operation': 'scene.capture', 'arguments': {'session_id': 'other'}}})
            with patch.object(resources, 'owns', return_value=True):
                resources.check({'extension': {'operation': 'scene.capture', 'arguments': {'session_id': 'mine', 'cameras': [{'path': '/World/Camera'}]}}})

    # 验证无世界归属的全局修改及服务本地来源路径均被拒绝。
    def test_global_world_mutation_and_local_source_paths_are_rejected(self):
        access = TrustedAccess(AccessScope('host','session','workspace','owner',1,1), '/tmp/workspace')
        with access_context(access), patch('capabilities.isaac_sim.resource_access.engine_for'):
            resources = NativeResources('127.0.0.1:50051')
            with self.assertRaises(PermissionError):
                resources.check({'step': {'count': 10}})
            with self.assertRaises(PermissionError):
                resources.check({'extension': {'operation': 'asset.import', 'arguments': {'source': '/service/secret.usd'}}})

    # 验证 USD 场景树中的绝对节点路径不会被误当作主机文件路径。
    def test_usd_prim_paths_are_not_host_paths(self):
        from service_access.input_policy import check_arguments
        access = TrustedAccess(AccessScope('host','session','workspace','owner',1,1), '/tmp/workspace')
        with access_context(access):
            check_arguments({'request': {'path': '/World/Robot', 'config': {'stiffness': 10}}})
