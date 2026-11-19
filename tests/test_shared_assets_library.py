"""Material/texture reference closure must not escape a published bundle."""

import json
from pathlib import Path
import struct
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from capabilities.asset_generation.shared_library import verify_reference_closure, verify_bundle, publish_generated, snapshot_input
from database.access import AccessScope
from service_access.access_scope import TrustedAccess


# 把 JSON 文档编码为最小对齐 GLB 容器，供依赖引用解析测试。
def glb(document):
    raw = json.dumps(document).encode()
    raw += b" " * (-len(raw) % 4)
    return struct.pack("<5I", 0x46546C67, 2, 20 + len(raw), len(raw), 0x4E4F534A) + raw


class LibraryClosureTests(unittest.TestCase):
    # 创建独立临时资源目录，隔离清单闭包与发布快照测试。
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    # 验证外部、绝对及编码后的父目录依赖引用全部被拒绝。
    def test_external_absolute_and_encoded_parent_references_are_rejected(self):
        path = self.root / "mesh.gltf"
        for reference in ("../secret.png", "%2e%2e/secret.png", "file:///etc/passwd", "https://host/a.png", "/other/image.png"):
            path.write_text(json.dumps({"images": [{"uri": reference}]}))
            with self.assertRaises((ValueError, FileNotFoundError), msg=reference):
                verify_reference_closure(path)

    # 验证资源的全部纹理依赖都经过存在性校验并纳入内容摘要。
    def test_all_texture_files_are_verified_and_hashed(self):
        (self.root / "textures").mkdir()
        (self.root / "textures/base.png").write_bytes(b"texture")
        (self.root / "asset.glb").write_bytes(glb({"asset": {"version": "2.0"}, "images": [{"uri": "textures/base.png"}]}))
        (self.root / "mesh.json").write_text(json.dumps({"materials": [{"textures": {"base_color": "textures/base.png"}}]}))
        manifest = verify_bundle(self.root)
        self.assertEqual(len(manifest), 3)
        self.assertTrue(all(len(row["sha256"]) == 64 for row in manifest))
        (self.root / "textures/base.png").unlink()
        with self.assertRaises(FileNotFoundError):
            verify_bundle(self.root)

    # 验证发布复制不可变快照，并只授予当前所有者而非默认公开。
    def test_publication_snapshots_files_and_grants_owner_not_public(self):
        source = self.root / "generated"
        source.mkdir()
        (source / "asset.glb").write_bytes(glb({"asset": {"version": "2.0"}}))
        (source / "mesh.json").write_text('{"materials":[]}')
        record = {"asset_id": "asset_0123456789abcdef", "label": "chair", "status": "generated",
                  "prompt": "private conversation", "source_work": "/private/task", "provenance": {"private": True}}
        (source / "asset.json").write_text(json.dumps(record))
        workspace = self.root / "workspace"
        workspace.mkdir()
        access = TrustedAccess(AccessScope("host", "session", "workspace", "owner", 1, 1), str(workspace))
        # 恢复测试快照文件的可写权限，确保临时目录能够正常清理。
        def writable_cleanup():
            for file in self.root.rglob("*"):
                if file.is_file():
                    file.chmod(0o600)
        self.addCleanup(writable_cleanup)
        with patch("capabilities.asset_generation.shared_library.validate_scope"), \
             patch("capabilities.asset_generation.shared_library.publish_version", return_value={"asset_version_id": "version-a"}) as publish, \
             patch("capabilities.asset_generation.shared_library.grant_version") as grant:
            publish_generated(self.root, access, source, record, engine=MagicMock(), run_id="run-a")
        kwargs = publish.call_args.kwargs
        saved = self.root / ".shared-assets" / kwargs["storage_ref"]
        metadata = json.loads((saved / "asset.json").read_text())
        self.assertNotIn("prompt", metadata)
        self.assertNotIn("source_work", metadata)
        self.assertNotIn("provenance", metadata)
        self.assertEqual(kwargs["provenance"]["run_id"], "run-a")
        self.assertEqual(grant.call_args.kwargs["grantee_owner_id"], "owner")
        self.assertNotIn("public", grant.call_args.kwargs)
        (source / "mesh.json").write_text('{"tampered":true}')
        self.assertNotIn("tampered", (saved / "mesh.json").read_text())

    # 验证输入快照拒绝链接，并将完整依赖闭包保存在私有暂存区。
    @unittest.skipUnless(os.name == "posix", "Descriptor-confined input copying requires Linux")
    def test_input_snapshot_rejects_symlink_and_keeps_reference_closure_private(self):
        workspace = self.root / "workspace"
        workspace.mkdir()
        (workspace / "image.png").write_bytes(b"source")
        (workspace / "mesh.gltf").write_text(json.dumps({"images": [{"uri": "image.png"}]}))
        access = TrustedAccess(AccessScope("host", "session", "workspace", "owner", 1, 1), str(workspace))
        saved = snapshot_input(self.root / "private", access, workspace / "mesh.gltf")
        self.assertEqual((saved.parent / "image.png").read_bytes(), b"source")
        (workspace / "image.png").unlink()
        (workspace / "image.png").symlink_to(self.root / "private-file")
        (self.root / "private-file").write_bytes(b"secret")
        with self.assertRaises(OSError):
            snapshot_input(self.root / "private", access, workspace / "mesh.gltf")
