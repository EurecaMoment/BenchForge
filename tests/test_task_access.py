"""Queued ownership cannot silently become an unscoped operator task."""

import unittest
from unittest.mock import MagicMock, patch

from database.access import AccessScope
from service_access.access_scope import TrustedAccess, access_context
from service_access.task_access import task_access


class TaskAccessTests(unittest.TestCase):
    def setUp(self):
        self.scope = AccessScope('host', 'session-a', 'workspace-a', 'owner', 1, 1)
        self.access = TrustedAccess(self.scope, '/workspaces/session-a')
        self.identity = {'host_id': 'host', 'owner_id': 'owner', 'root_path': self.access.root_path}

    def engine(self, row, identity=None):
        engine = MagicMock()
        connection = engine.connect.return_value.__enter__.return_value
        connection.execute.return_value.mappings.return_value.first.side_effect = [
            row, self.identity if identity is None else identity]
        return engine

    def row(self, **changes):
        return {'workspace_id': None, 'session_id': None, 'plan': {'context': {}},
                'task_unit': {'run_context': {}}, **changes}

    def test_explicit_operator_path_requires_absence_of_all_conversation_identity(self):
        row = self.row()
        self.assertIsNone(task_access(self.engine(row), 'operator.scene0'))
        with access_context(self.access), self.assertRaises(PermissionError):
            task_access(self.engine(row), 'operator.scene0')

    def test_each_persisted_dsh_context_restores_ownership(self):
        context = {'workspace_id': 'workspace-a', 'session_id': 'session-a'}
        for row in (self.row(**context), self.row(plan={'context': context}),
                    self.row(task_unit={'run_context': context})):
            with self.subTest(row=row), patch('service_access.task_access.resolve_access_scope', return_value=self.scope):
                self.assertEqual(task_access(self.engine(row), 'run.scene0'), self.access)

    def test_missing_partial_and_conflicting_ownership_fail_closed(self):
        rows = (None, self.row(workspace_id='workspace-a'), self.row(session_id='session-a'),
                self.row(workspace_id='workspace-a', session_id='session-a',
                         plan={'context': {'workspace_id': 'workspace-b', 'session_id': 'session-b'}}),
                self.row(workspace_id='workspace-a', session_id='session-a',
                         task_unit={'run_context': {'workspace_id': 'workspace-b', 'session_id': 'session-a'}}))
        for row in rows:
            with self.subTest(row=row), patch('service_access.task_access.resolve_access_scope', return_value=self.scope):
                with self.assertRaises(PermissionError):
                    task_access(self.engine(row), 'run.scene0')

    def test_active_conversation_cannot_switch_to_a_different_queued_owner(self):
        other = TrustedAccess(AccessScope('host', 'session-b', 'workspace-b', 'owner', 1, 1), '/workspaces/session-b')
        with (access_context(other), patch('service_access.task_access.resolve_access_scope', return_value=self.scope),
              self.assertRaises(PermissionError)):
            task_access(self.engine(self.row(workspace_id='workspace-a', session_id='session-a')), 'run.scene0')

    def test_asset_library_retains_compatibility_export_only(self):
        from capabilities.asset_generation.shared_library import task_access as compatibility
        self.assertIs(compatibility, task_access)

    def store(self):
        from storage.scene_jobs.store import Store
        store = object.__new__(Store)
        store.lifecycle = MagicMock()
        store.repo = MagicMock()
        from database.schema import request_keys
        store.keys = request_keys
        connection = store.repo.transaction.return_value.__enter__.return_value
        connection.execute.return_value.mappings.return_value.first.return_value = None
        return store, connection

    def test_dsh_submit_persists_host_identity_even_if_caller_omits_context(self):
        store, connection = self.store()
        with access_context(self.access), patch('storage.scene_jobs.store.lock_entity'):
            store.submit('request-a', [{'name': 'native capture'}])
        inserts = [call.args[0].compile().params for call in connection.execute.call_args_list
                   if getattr(call.args[0], 'is_insert', False)]
        plan = next(values['plan'] for values in inserts if 'plan' in values)
        unit = next(values['unit'] for values in inserts if 'unit' in values)
        self.assertEqual(plan['context']['workspace_id'], 'workspace-a')
        self.assertEqual(plan['context']['session_id'], 'session-a')
        self.assertEqual(unit['run_context'], plan['context'])

    def test_dsh_submit_rejects_conflicting_context_before_admission(self):
        store, _ = self.store()
        with access_context(self.access), self.assertRaises(PermissionError):
            store.submit('request-a', [{}], context={'session_id': 'session-b'})
        store.lifecycle.admit.assert_not_called()
        store.repo.transaction.assert_not_called()
