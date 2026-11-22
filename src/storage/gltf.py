"""Read glTF metadata without resolving assets or loading mesh buffers."""

import json
import struct


# 读取 glTF 文档或有界的 GLB JSON 块，避免加载几何与纹理数据。
def gltf_document(path):
    if path.suffix.lower() == ".gltf":
        return json.loads(path.read_text(encoding="utf-8"))
    with path.open("rb") as source:
        header = source.read(20)
        if len(header) != 20:
            raise ValueError("Truncated GLB")
        magic, version, length, chunk_length, chunk_type = struct.unpack("<5I", header)
        if (magic != 0x46546C67 or version != 2 or length != path.stat().st_size
                or chunk_type != 0x4E4F534A or chunk_length > min(length - 20, 32 * 1024 * 1024)):
            raise ValueError("Invalid GLB header")
        return json.loads(source.read(chunk_length).decode("utf-8"))
