"""Nested scene plans and prior capture references retain conversation scope."""

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, patch

from capabilities.scene_authoring.contracts.scene_design import (
    saved_design, validate_brief_revision, plan_declaration_current,
)
from database.access import AccessScope
from service_access.access_scope import TrustedAccess, access_context
from service_access.resource_files import read_owned_resource, resolve_owned_resource


class SceneDesignAccessTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.workspace = self.root / 'workspace-a'
        self.workspace.mkdir()
        self.artifacts = self.root / 'artifacts'
        self.artifacts.mkdir()
        self.access = TrustedAccess(
            AccessScope('host', 'session-a', 'workspace-a', 'owner', 1, 1), str(self.workspace))

    def plan(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'schema': 'benchforge.assessment/v1',
                                   'scenes': {'designs': [{'id': 'room'}]}}), encoding='utf-8')
        return {'provenance': {'sources': [
            {'kind': 'scene_plan', 'path': str(path), 'scene_design_id': 'room'}]}}

    def test_foreign_plan_is_denied_before_parsing(self):
        program = self.plan(self.root / 'workspace-b/plan.json')
        with access_context(self.access), patch.dict(os.environ, {'BENCHFORGE_ARTIFACTS': str(self.artifacts)}):
            with self.assertRaises(PermissionError):
                saved_design(program)

    def test_foreign_brief_directory_is_denied(self):
        foreign = self.root / 'workspace-b/capture'
        foreign.mkdir(parents=True)
        (foreign / 'scene_brief.json').write_text('{}', encoding='utf-8')
        design = {'previous_capture_directory': str(foreign), 'revision_history': []}
        with access_context(self.access), self.assertRaises(PermissionError):
            validate_brief_revision(design, self.workspace / 'plan.json', self.artifacts)

    def test_matching_version_cannot_bypass_plan_ownership(self):
        path = self.root / 'workspace-b/plan.json'
        self.plan(path)
        declaration = {'plan_path': str(path),
                       'plan_version': [path.stat().st_size, path.stat().st_mtime_ns]}
        with access_context(self.access):
            self.assertFalse(plan_declaration_current(declaration))

    def test_production_resource_requires_matching_database_owner(self):
        path = self.artifacts / 'sf_private.scene0/revision_0/scene_brief.json'
        path.parent.mkdir(parents=True)
        path.write_text('{}', encoding='utf-8')
        engine = MagicMock()
        connection = engine.connect.return_value.__enter__.return_value
        with access_context(self.access), patch('service_access.resource_files.validate_scope'):
            for owner in (None, {'workspace_id': 'workspace-b', 'session_id': 'session-b'},
                          {'workspace_id': 'workspace-a', 'session_id': 'session-b'}):
                connection.execute.return_value.mappings.return_value.first.return_value = owner
                with self.subTest(owner=owner), self.assertRaises(PermissionError):
                    resolve_owned_resource(engine, self.artifacts, path)
            connection.execute.return_value.mappings.return_value.first.return_value = {
                'workspace_id': 'workspace-a', 'session_id': 'session-a'}
            self.assertEqual(resolve_owned_resource(engine, self.artifacts, path), path.resolve())

    @unittest.skipUnless(os.name == 'posix', 'Descriptor-relative file reads require Linux')
    def test_own_plan_and_prior_evidence_remain_usable(self):
        path = self.workspace / 'plan.json'
        program = self.plan(path)
        previous = self.workspace / 'previous'
        (previous / 'capture').mkdir(parents=True)
        (previous / 'scene_brief.json').write_text('{"hard_requirements": ["retain"], "revision_history": []}', encoding='utf-8')
        (previous / 'capture/view_0.png').write_bytes(b'OWN_EVIDENCE')
        (previous / 'capture/evidence.json').write_text('{}', encoding='utf-8')
        design = {'previous_capture_directory': str(previous), 'hard_requirements': ['retain'],
                  'revision_history': [{'revision': 1, 'evidence_directory': str(previous),
                       'changes': ['repaired position'], 'unresolved': [], 'evidence_refs': [
                           {'path': 'capture/view_0.png', 'observation': 'image'},
                           {'path': 'capture/evidence.json', 'observation': 'physical evidence'}]}]}
        with access_context(self.access):
            self.assertEqual(saved_design(program)[1]['id'], 'room')
            validate_brief_revision(design, path)

    @unittest.skipUnless(os.name == 'posix', 'Descriptor-relative file reads require Linux')
    def test_plan_symlink_cannot_hide_foreign_file(self):
        foreign = self.root / 'workspace-b/plan.json'
        self.plan(foreign)
        link = self.workspace / 'plan.json'
        link.symlink_to(foreign)
        with access_context(self.access), self.assertRaises(PermissionError):
            saved_design({'provenance': {'sources': [
                {'kind': 'scene_plan', 'path': str(link), 'scene_design_id': 'room'}]}})

    @unittest.skipUnless(os.name == 'posix', 'Descriptor-relative file reads require Linux')
    def test_private_owned_evidence_uses_checked_file_descriptor(self):
        path = self.artifacts / 'sf_owned.scene0/revision_0/scene_brief.json'
        path.parent.mkdir(parents=True)
        path.write_bytes(b'{"owned":true}')
        engine = MagicMock()
        connection = engine.connect.return_value.__enter__.return_value
        connection.execute.return_value.mappings.return_value.first.return_value = {
            'workspace_id': 'workspace-a', 'session_id': 'session-a'}
        with access_context(self.access), patch('service_access.resource_files.validate_scope'):
            content, version = read_owned_resource(path, limit=1024, engine=engine, artifact_root=self.artifacts)
        self.assertEqual(content, b'{"owned":true}')
        self.assertEqual(version[0], len(content))
