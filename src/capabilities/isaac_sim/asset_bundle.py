"""Freeze and upload a scene layer together with its texture dependencies."""

from __future__ import annotations

import hashlib
import json
import shutil
import uuid
from pathlib import Path

from storage.files import safe_path, write_json


# 将可读 USDA 层无损导出为紧凑 USDC，保留同目录贴图引用并降低 gRPC 上传体积。
def export_binary_usd(source, destination):
    source, destination = Path(source), Path(destination)
    if source.parent.resolve() != destination.parent.resolve() or destination.suffix.lower() != '.usdc':
        raise ValueError('Binary USD must be exported beside its source as a .usdc layer')
    from pxr import Usd

    stage = Usd.Stage.Open(str(source))
    if stage is None:
        raise ValueError('A100 scene compiler did not produce a readable USD layer')
    if not stage.GetRootLayer().Export(str(destination)):
        raise ValueError('OpenUSD could not export the scene as binary USDC')
    return destination


def write_mesh_layer(scene_text, packed_meshes, destination):
    """Populate typed mesh buffers without formatting and reparsing millions of USDA literals."""
    import numpy as np
    from pxr import Sdf

    layer = Sdf.Layer.CreateAnonymous('.usda')
    if not layer.ImportFromString(scene_text):
        raise ValueError('Invalid compiled USD layer')
    for prim_path, attributes in packed_meshes.items():
        for name, values in attributes.items():
            spec = layer.GetAttributeAtPath(prim_path + '.' + name)
            dtype = np.int32 if spec.typeName == Sdf.ValueTypeNames.IntArray else np.float32
            data = np.asarray(values, dtype=dtype, order='C')
            if dtype == np.float32 and not np.isfinite(data).all():
                raise ValueError('USD numbers must be finite')
            spec.default = type(spec.typeName.defaultValue).FromNumpy(data)
    if not layer.Export(str(destination)):
        raise ValueError('OpenUSD could not export the scene as binary USDC')
    return destination


# 按路径稳定暂存贴图依赖，并应用 OmniPBR ORM 粗糙度贴图的金属度因子。
def stage_texture_dependencies(dependencies, directory):
    staged = []
    for dependency in dependencies:
        relative = dependency['relative_path']
        source = Path(dependency['source_path'])
        target = safe_path(directory, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name('.' + target.name + '.part')
        factor = dependency.get('metallic_factor', 1.0)
        try:
            if factor == 1:
                shutil.copyfile(source, temporary)
            else:
                import numpy as np
                from PIL import Image

                with Image.open(source) as image:
                    pixels = np.asarray(image).copy()
                    image_format = image.format
                if pixels.ndim != 3 or pixels.shape[2] < 3:
                    raise ValueError('Metallic-roughness texture must contain RGB channels')
                pixels[:, :, 2] = np.rint(
                    pixels[:, :, 2].astype(np.float32) * factor
                ).astype(pixels.dtype)
                Image.fromarray(pixels).save(temporary, format=image_format)
            if target.exists():
                with temporary.open('rb') as stream:
                    temporary_digest = hashlib.file_digest(stream, 'sha256').digest()
                with target.open('rb') as stream:
                    target_digest = hashlib.file_digest(stream, 'sha256').digest()
                if temporary_digest != target_digest:
                    raise ValueError('Frozen A100 texture dependency changed after scene compilation')
                temporary.unlink()
            else:
                temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
        staged.append({'relative_path': relative, 'path': target})
    return staged


# 对场景和侧车文件生成不可变清单，并核验每个 A100 上传回执。
def upload_asset_bundle(client, source, directory, dependencies=()):
    receipt = directory / 'asset.json'
    files = [{'relative_path': source.name, 'path': source}]
    files.extend(dependencies)
    manifest = []
    for item in files:
        path = safe_path(directory, item['relative_path'], must_exist=True)
        with path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        manifest.append({'relative_path': item['relative_path'], 'sha256': digest,
                         'size_bytes': path.stat().st_size})
    manifest.sort(key=lambda row: row['relative_path'])
    if len({row['relative_path'] for row in manifest}) != len(manifest):
        raise ValueError('A100 asset bundle contains duplicate relative paths')
    digest = next(row['sha256'] for row in manifest if row['relative_path'] == source.name)
    if receipt.exists():
        value = json.loads(receipt.read_text())
        previous = value.get('files')
        if previous is None:
            if not dependencies and value.get('sha256') == digest:
                return {key: value[key] for key in ('asset_id', 'relative_path')}
            raise ValueError('Frozen A100 asset receipt predates texture bundle support')
        if value.get('sha256') != digest or previous != manifest:
            raise ValueError('Frozen scene asset changed after upload')
        return {key: value[key] for key in ('asset_id', 'relative_path')}

    # Upload has no deduplication RPC. A lost reply can leave an unused immutable file.
    asset_id = 'benchforge-' + uuid.uuid4().hex
    for item in files:
        path = safe_path(directory, item['relative_path'], must_exist=True)
        uploaded = client.upload(path, asset_id=asset_id, relative_path=item['relative_path'])
        expected = next(row for row in manifest if row['relative_path'] == item['relative_path'])
        uploaded_size = uploaded.get('size_bytes')
        if isinstance(uploaded_size, str) and uploaded_size.isdecimal():
            uploaded_size = int(uploaded_size)
        if (uploaded.get('asset_id') != asset_id or uploaded.get('relative_path') != item['relative_path']
                or uploaded.get('sha256') != expected['sha256']
                or type(uploaded_size) is not int or uploaded_size != expected['size_bytes']):
            raise ValueError('A100 asset bundle upload receipt does not match its frozen file')
    value = {'asset_id': asset_id, 'relative_path': source.name,
             'sha256': digest, 'files': manifest}
    write_json(receipt, value)
    return {key: value[key] for key in ('asset_id', 'relative_path')}
