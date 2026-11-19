"""Host identity, tamper detection and conversation file boundaries."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import sys
from unittest.mock import patch
from database.access import AccessScope
from service_access.access_scope import (
    TrustedAccess, access_context, current_access, decode_ticket, sign_ticket, workspace_path,
)
from service_access.input_policy import check_arguments, resolve_arguments


class AccessScopeTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.access = TrustedAccess(AccessScope('host', 'session-a', 'workspace-a', 'owner', 1, 1), str(self.root))

    def test_signed_scope_tampering_and_expiration(self):
        with patch('service_access.access_scope.time.time', return_value=1000):
            ticket = sign_ticket(self.access, 'secret')
        self.assertEqual(decode_ticket(ticket, 'secret', now=1001), self.access)
        for invalid in (ticket + '0', 'invalid', ticket.replace('.', 'x', 1)):
            with self.assertRaises(PermissionError):
                decode_ticket(invalid, 'secret', now=1001)
        with self.assertRaises(PermissionError):
            decode_ticket(ticket, 'secret', now=1300)
        with self.assertRaises(PermissionError):
            decode_ticket(ticket, 'other-key', now=1001)

    def test_context_is_restored_and_paths_cannot_escape(self):
        with access_context(self.access):
            self.assertEqual(workspace_path('tasks/new.json', must_exist=False), self.root / 'tasks/new.json')
            with self.assertRaises(PermissionError):
                workspace_path('../outside', must_exist=False)
            with self.assertRaises(PermissionError):
                check_arguments({'plan': str(self.root.parent / 'other/plan.json')})
            with self.assertRaises(PermissionError):
                check_arguments({'_dsh_context': {'session_id': 'session-b'}})
            check_arguments({'plan': 'tasks/plan.json', 'description': 'Read /etc as a textual example'})
        self.assertIsNone(current_access())

    @unittest.skipUnless(sys.platform == 'linux', 'Scoped task directories require Linux descriptors')
    def test_task_labels_are_not_global_workspaces(self):
        from server.task_workspace import task_directory
        with access_context(self.access):
            self.assertEqual(task_directory('asset-work'), self.root / 'tasks/asset-work/work')
            with self.assertRaises(ValueError):
                task_directory('../session-b')

    def test_task_paths_resolve_in_workspace_independently_of_host_import_directory(self):
        request = {'plan': 'tasks/plan.json', 'run': 'tasks/run', 'description': '/example/text',
                   'source_image': 'https://example.test/image.png',
                   'request': {'source': {'asset_id': 'remote-asset'}}}
        with access_context(self.access):
            resolved = resolve_arguments(request, self.root)
            self.assertEqual(resolved['plan'], str(self.root / 'tasks/plan.json'))
            self.assertEqual(resolved['run'], str(self.root / 'tasks/run'))
            self.assertEqual(resolved['request'], request['request'])
            self.assertEqual(resolved['source_image'], request['source_image'])
            self.assertEqual(resolved['description'], request['description'])
            self.assertEqual(request['plan'], 'tasks/plan.json')
            with self.assertRaises(PermissionError):
                resolve_arguments({'plan': '../outside.json'}, self.root)

    def test_asset_member_name_is_not_resolved_as_a_workspace_file(self):
        """上传来源绑定本地工作区，资产内部路径和句柄必须原样传递。"""
        request = {'action': 'upload', 'asset_id': 'owned-asset',
                   'relative_path': 'meshes/rotor.stl',
                   'source_path': 'tasks/drone_villa/work/drone/rotor.stl'}
        for access in (self.access, None):
            with self.subTest(scoped=access is not None), access_context(access):
                resolved = resolve_arguments(request, self.root)
                self.assertEqual(resolved['source_path'], str(self.root / request['source_path']))
                self.assertEqual(resolved['relative_path'], 'meshes/rotor.stl')
                self.assertEqual(resolved['asset_id'], 'owned-asset')
        self.assertEqual(request['source_path'], 'tasks/drone_villa/work/drone/rotor.stl')
        with access_context(self.access), self.assertRaises(PermissionError):
            resolve_arguments({**request, 'source_path': '../another-session/rotor.stl'}, self.root)
