"""Read registered assets without constructing generation clients or directories."""
from pathlib import Path
import re


def registered_asset_path(artifact_root, asset_id, filename='asset.json', *, access=None, engine=None):
    """按明确访问身份解析登记资产文件，查询不创建目录、不启动生成服务。"""
    if not isinstance(asset_id, str) or not re.fullmatch(r'asset_[a-f0-9]{16}', asset_id):
        raise ValueError('invalid registered asset ID')
    if access is not None:
        from capabilities.asset_generation.shared_library import asset_file
        return asset_file(Path(artifact_root).resolve(), access, asset_id, filename, engine=engine)
    root = Path(artifact_root).resolve() / 'generated_assets' / asset_id
    path = (root / filename).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError('registered asset file is unavailable')
    return path
