"""Pure data contract for a session-uploaded native scene asset."""

import math
import re
from urllib.parse import unquote


def validate_native_reference(value):
    """Validate a native handle, never a simulator or service filesystem path."""
    required = {'asset_id', 'relative_path', 'size', 'source_up_axis'}
    if not isinstance(value, dict) or not required <= set(value) <= required | {'fix_base'}:
        raise ValueError('native_asset requires asset_id, relative_path, size and source_up_axis; '
                         'only optional fix_base is supported')
    identity = value['asset_id']
    if not isinstance(identity, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,511}', identity):
        raise ValueError('native_asset.asset_id must be an uploaded asset handle')
    path = value['relative_path']
    if (not isinstance(path, str) or not path or len(path) > 1024
            or any(character in path for character in ('\\', ':', '\x00'))
            or any(part in ('', '.', '..') for part in path.split('/'))
            or unquote(path) != path):
        raise ValueError('native_asset.relative_path must be a relative file inside the uploaded asset')
    size = value['size']
    if (not isinstance(size, list) or len(size) != 3
            or any(type(number) not in (int, float) or not math.isfinite(number)
                   or not 0 < number <= 1000 for number in size)):
        raise ValueError('native_asset.size must contain three finite positive source dimensions in meters')
    if value['source_up_axis'] not in ('Y', 'Z'):
        raise ValueError('native_asset.source_up_axis must be Y or Z')
    if 'fix_base' in value and type(value['fix_base']) is not bool:
        raise ValueError('native_asset.fix_base must be boolean')
    return value


def native_size_z_up(value):
    """Declared source dimensions after the native import's up-axis conversion."""
    validate_native_reference(value)
    x, y, z = value['size']
    return [x, z, y] if value['source_up_axis'] == 'Y' else [x, y, z]


def native_references(program):
    """Return explicit object handles; unrelated metadata is not an import."""
    if not isinstance(program, dict) or not isinstance(program.get('objects'), list):
        return []
    return [(index, obj) for index, obj in enumerate(program['objects'])
            if isinstance(obj, dict) and obj.get('kind') == 'native']
