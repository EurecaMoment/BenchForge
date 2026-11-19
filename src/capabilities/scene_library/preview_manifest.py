"""Read the immutable public preview manifest without invoking an encoder."""

import json
from pathlib import Path
import re


PREVIEW_FILE = "preview/scene.mp4"
FORMAT_VERSION = 1


# 校验任务与非负修订身份，返回独立场景预览目录。
def preview_folder(config, task_id, revision):
    if (
        not re.fullmatch(r"sf_[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+", task_id)
        or type(revision) is not int
        or revision < 0
    ):
        raise ValueError("Invalid scene preview identity")
    return Path(config["artifacts"]) / "scene-previews" / task_id / ("revision_" + str(revision))


# 验证预览清单身份和视频大小等条件，返回有效缓存或空值。
def cached_preview(config, task_id, revision):
    folder = preview_folder(config, task_id, revision)
    try:
        saved = json.loads((folder / "manifest.json").read_text())
        movie = folder / "scene.mp4"
        if (
            saved.get("version") != FORMAT_VERSION
            or saved.get("task_id") != task_id
            or saved.get("revision") != revision
        ):
            return None
        if (
            movie.is_symlink()
            or movie.stat().st_size != saved.get("bytes")
            or movie.stat().st_size < 32
        ):
            return None
        return {**saved, "source_path": str(movie), "content_type": "video/mp4"}
    except (OSError, ValueError):
        return None
