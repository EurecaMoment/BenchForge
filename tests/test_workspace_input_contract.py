"""One conversation namespace across scene, mesh and task-code inputs."""

from contextlib import chdir
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from capabilities.asset_generation.generation import GenerationTools
from capabilities.scene_authoring.program_submission import read_program_document
from database.access import AccessScope
from server.access_requests import authorize_request
from service_access.access_scope import TrustedAccess, access_context


class WorkspaceInputContractTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.workspace = self.root / 'session-a'
        self.workspace.mkdir()
        self.artifacts = self.root / 'private'
        self.artifacts.mkdir()
        self.access = TrustedAccess(AccessScope('host', 'session-a', 'workspace-a', 'owner', 1, 1),
                                    str(self.workspace))
        self.store = Mock(root=self.artifacts)

        def writable_cleanup():
            for file in self.artifacts.rglob('*'):
                if file.is_file():
                    file.chmod(0o600)
        self.addCleanup(writable_cleanup)

    @unittest.skipUnless(os.name == 'posix', 'Confined input reads require Linux descriptors')
    def test_relative_scene_and_obj_inputs_ignore_backend_cwd(self):
        task = self.workspace / 'tasks/prepare/work'
        task.mkdir(parents=True)
        scene = task / 'scene.json'
        scene.write_text(json.dumps({'objects': []}))
        mesh = task / 'mesh.obj'
        mesh.write_text('mtllib material.mtl\nv 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n')
        (task / 'material.mtl').write_text('newmtl surface\nmap_Kd texture.png\n')
        (task / 'texture.png').write_bytes(b'texture')
        decoy = self.root / 'backend/tasks/prepare/work'
        decoy.mkdir(parents=True)
        (decoy / 'scene.json').write_text('{"wrong_source":true}')
        (decoy / 'mesh.obj').write_text('wrong_source')

        with chdir(self.root / 'backend'), access_context(self.access):
            request = {'scene_program_path': 'tasks/prepare/work/scene.json'}
            self.assertEqual(authorize_request(self.store, '/scene-preflight', request), request)
            submission = read_program_document(self.artifacts, request['scene_program_path'])
            self.assertEqual(submission['program'], {'objects': []})
            self.assertEqual(submission['source_path'], str(scene))
            tools = GenerationTools(self.artifacts)
            imported = tools.source_path('tasks/prepare/work/mesh.obj')
            self.assertEqual(imported.read_bytes(), mesh.read_bytes())
            self.assertEqual((imported.parent / 'texture.png').read_bytes(), b'texture')
            self.assertEqual(tools.source_path(imported), imported)

    def test_missing_relative_input_never_retries_backend_directory(self):
        (self.root / 'scene.json').write_text('{"wrong_source":true}')
        (self.root / 'mesh.obj').write_text('v 0 0 0')
        with chdir(self.root), access_context(self.access):
            tools = GenerationTools(self.artifacts)
            for consumer, file in ((tools.source_path, 'mesh.obj'),
                                   (lambda value: read_program_document(self.artifacts, value), 'scene.json')):
                with self.subTest(file=file), self.assertRaises(FileNotFoundError):
                    consumer(file)

    def test_cross_session_source_and_production_scene_paths_are_denied(self):
        with access_context(self.access):
            tools = GenerationTools(self.artifacts, engine=Mock())
            for name in ('session-b', 'source', 'private/task-a'):
                folder = self.root / name
                folder.mkdir(parents=True)
                scene = folder / 'scene.json'
                scene.write_text('{"objects":[]}')
                mesh = folder / 'mesh.obj'
                mesh.write_text('v 0 0 0')
                with self.subTest(name=name):
                    with self.assertRaises(PermissionError):
                        read_program_document(self.artifacts, str(scene))
                    with patch('server.access_requests.require_run') as check_run:
                        with self.assertRaises(PermissionError):
                            authorize_request(self.store, '/scene-preflight', {'scene_program_path': str(scene)})
                        check_run.assert_not_called()
                    foreign = TrustedAccess(AccessScope('host', 'session-b', 'workspace-b', 'owner', 1, 1),
                                            str(folder))
                    with patch('service_access.task_access.task_access', return_value=foreign):
                        with self.assertRaises(PermissionError):
                            tools.source_path(str(mesh))

    @unittest.skipUnless(os.name == 'posix', 'Symbolic link regression requires Linux')
    def test_linked_workspace_input_does_not_fall_through_to_owned_artifact(self):
        source = self.artifacts / 'task-a/mesh.obj'
        source.parent.mkdir()
        source.write_text('v 0 0 0')
        (self.workspace / 'mesh.obj').symlink_to(source)
        with access_context(self.access):
            tools = GenerationTools(self.artifacts)
            with patch('service_access.task_access.task_access') as task_access:
                for path in ('mesh.obj', str(self.workspace / 'mesh.obj')):
                    with self.subTest(path=path), self.assertRaises(PermissionError):
                        tools.source_path(path)
                task_access.assert_not_called()
