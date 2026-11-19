"""Manifest inputs cannot widen filesystem access or masquerade as hidden GT."""

import unittest
from unittest.mock import MagicMock
from database.access import AccessScope
from storage.shared_assets import validate_manifest, publish_version, grant_version


# 构造带固定摘要和大小的合法资源清单条目。
def entry(path="mesh.glb"):
    return {"path": path, "sha256": "a" * 64, "bytes": 100}


class ManifestTests(unittest.TestCase):
    # 验证资源清单只接受规范相对文件路径，并拒绝重复项。
    def test_relative_files_only_and_duplicate_rejection(self):
        for path in ("/etc/passwd", "../a", "a/../b", "a//b", "C:/private", "a\\b", "a/./b", "a\x00b"):
            with self.assertRaises(ValueError, msg=path):
                validate_manifest([entry(path)])
        with self.assertRaises(ValueError):
            validate_manifest([entry(), entry()])
        with self.assertRaises(ValueError):
            validate_manifest([{**entry(), "symlink": "../../other"}])
        self.assertEqual(validate_manifest([entry("z"), entry("a")])[0]["path"], "a")

    # 验证发布拒绝隐藏评测权威分类和绝对存储位置。
    def test_publish_never_accepts_hidden_authority_or_absolute_storage(self):
        engine = MagicMock()
        scope = AccessScope("h", "s", "w", "o", 1, 1)
        for classification in ("authority", "private_gt", "answers"):
            with self.assertRaises(ValueError):
                publish_version(engine, scope, name="x", kind="mesh", manifest=[entry()],
                    storage_ref="objects/123", metadata={"classification": classification})
        with self.assertRaises(ValueError):
            publish_version(engine, scope, name="x", kind="mesh", manifest=[entry()], storage_ref="/root/private")
        engine.begin.assert_not_called()

    # 验证每条授权必须且只能指定一种公开、所有者或工作区受众。
    def test_grant_has_exactly_one_audience(self):
        engine = MagicMock()
        scope = AccessScope("h", "s", "w", "o", 1, 1)
        for options in ({}, {"public": True, "grantee_workspace_id": "w2"}):
            with self.assertRaises(ValueError):
                grant_version(engine, scope, "v", **options)
        engine.begin.assert_not_called()


if __name__ == "__main__":
    unittest.main()
