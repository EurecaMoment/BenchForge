"""Nested scene plans and prior capture references retain conversation scope."""

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, patch

from capabilities.scene_authoring.contracts.scene_design import (
    saved_design, validate_brief_revision, plan_declaration_current, require_prior_brief,
)
from database.access import AccessScope
from service_access.access_scope import TrustedAccess, access_context
from service_access.resource_files import read_owned_resource, resolve_owned_resource


class SceneDesignAccessTests(unittest.TestCase):
    # 建立会话工作区、私有服务产物及可信身份，供场景设计来源授权验证。
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

    # 写入最小评估计划并返回引用该计划的场景程序夹具。
    def plan(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'schema': 'benchforge.assessment/v1',
                                   'scenes': {'designs': [{'id': 'room'}]}}), encoding='utf-8')
        return {'provenance': {'sources': [
            {'kind': 'scene_plan', 'path': str(path), 'scene_design_id': 'room'}]}}

    # 验证外部会话计划在解析内容前被拒绝访问。
    def test_foreign_plan_is_denied_before_parsing(self):
        program = self.plan(self.root / 'workspace-b/plan.json')
        with access_context(self.access), patch.dict(os.environ, {'BENCHFORGE_ARTIFACTS': str(self.artifacts)}):
            with self.assertRaises(PermissionError):
                saved_design(program)

    def test_execution_repair_inherits_the_unchanged_frozen_brief(self):
        directory = self.workspace / 'prior'
        directory.mkdir()
        design = {'id': 'room', 'hard_requirements': ['retain real objects']}
        frozen = {**design, 'plan_version': [120, 10], 'plan_path': 'old-plan.json'}
        (directory / 'scene_brief.json').write_text(json.dumps(frozen), encoding='utf-8')
        current = {**design, 'plan_version': [155, 12], 'plan_path': 'current-plan.json'}
        with access_context(self.access):
            require_prior_brief(current, directory, self.artifacts)
            with self.assertRaisesRegex(ValueError, 'reference the frozen brief'):
                require_prior_brief({**current, 'hard_requirements': []}, directory, self.artifacts)

    # 验证不能从其他会话的采集目录读取设计简报。
    def test_foreign_brief_directory_is_denied(self):
        foreign = self.root / 'workspace-b/capture'
        foreign.mkdir(parents=True)
        (foreign / 'scene_brief.json').write_text('{}', encoding='utf-8')
        design = {'previous_capture_directory': str(foreign), 'revision_history': []}
        with access_context(self.access), self.assertRaises(PermissionError):
            validate_brief_revision(design, self.workspace / 'plan.json', self.artifacts)

    # 验证匹配的文件版本不能替代计划所有权校验。
    def test_matching_version_cannot_bypass_plan_ownership(self):
        path = self.root / 'workspace-b/plan.json'
        self.plan(path)
        declaration = {'plan_path': str(path),
                       'plan_version': [path.stat().st_size, path.stat().st_mtime_ns]}
        with access_context(self.access):
            self.assertFalse(plan_declaration_current(declaration))

    # 验证访问生产资源须由数据库确认其所属会话。
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

    # 验证当前会话自己的计划与先前证据仍能正常复用。
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

    # 验证 Windows 短路径与长路径等价时，工作区及证据授权仍一致。
    @unittest.skipUnless(os.name == 'nt', 'Windows short-path aliases')
    def test_short_paths_preserve_workspace_and_evidence_ownership(self):
        import ctypes
        from ctypes import wintypes
        short_path = ctypes.WinDLL('kernel32', use_last_error=True).GetShortPathNameW
        short_path.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
        short_path.restype = wintypes.DWORD
        directory = self.artifacts / 'sf_owned.scene0/revision_0'
        directory.mkdir(parents=True)
        engine = MagicMock()
        connection = engine.connect.return_value.__enter__.return_value
        owner = connection.execute.return_value.mappings.return_value.first
        for path in (self.workspace / 'scene plan.json', directory / 'scene brief.json'):
            path.write_text('{}', encoding='utf-8')
            buffer = ctypes.create_unicode_buffer(32768)
            length = short_path(str(path.resolve()), buffer, len(buffer))
            self.assertGreater(length, 0)
            self.assertLess(length, len(buffer))
            alias = Path(buffer.value)
            if alias == path.resolve():
                self.skipTest('Filesystem has 8.3 short names disabled')
            owner.return_value = {'workspace_id': 'workspace-a', 'session_id': 'session-a'}
            with self.subTest(path=path), access_context(self.access), patch('service_access.resource_files.validate_scope'):
                self.assertEqual(resolve_owned_resource(engine, self.artifacts, alias), path.resolve())
                if path.parent == directory:
                    owner.return_value = {'workspace_id': 'workspace-a', 'session_id': 'session-b'}
                    with self.assertRaises(PermissionError):
                        resolve_owned_resource(engine, self.artifacts, alias)

    # 验证规范路径比较不会误授权经链接访问的自有证据。
    @unittest.skipUnless(os.name == 'posix', 'Symbolic link regression')
    def test_canonical_comparison_does_not_authorize_linked_owned_evidence(self):
        directory = self.artifacts / 'sf_owned.scene0/revision_0'
        directory.mkdir(parents=True)
        path = directory / 'scene_brief.json'
        path.write_text('{}', encoding='utf-8')
        link = self.root / 'evidence-link'
        link.symlink_to(directory, target_is_directory=True)
        engine = MagicMock()
        connection = engine.connect.return_value.__enter__.return_value
        connection.execute.return_value.mappings.return_value.first.return_value = {
            'workspace_id': 'workspace-a', 'session_id': 'session-a'}
        with access_context(self.access), patch('service_access.resource_files.validate_scope'):
            with self.assertRaises(PermissionError):
                resolve_owned_resource(engine, self.artifacts, link / path.name)

    # 验证计划符号链接不能隐藏其他会话的真实来源。
    @unittest.skipUnless(os.name == 'posix', 'Descriptor-relative file reads require Linux')
    def test_plan_symlink_cannot_hide_foreign_file(self):
        foreign = self.root / 'workspace-b/plan.json'
        self.plan(foreign)
        link = self.workspace / 'plan.json'
        link.symlink_to(foreign)
        with access_context(self.access), self.assertRaises(PermissionError):
            saved_design({'provenance': {'sources': [
                {'kind': 'scene_plan', 'path': str(link), 'scene_design_id': 'room'}]}})

    # 验证私有自有证据通过已校验的文件描述符读取，防止检查后替换。
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
