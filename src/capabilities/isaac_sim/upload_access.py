"""Validate inert asset references before uploading bytes to the shared simulator.

The remote importer must never resolve a task-authored reference outside its
owned asset directory. Parse individual files, without composing USD stages or
following dependencies; uploaded dependencies receive the same validation.
"""

from contextlib import contextmanager
import json
from pathlib import Path, PurePosixPath
import shlex
import stat
import struct
from tempfile import TemporaryDirectory
import xml.etree.ElementTree as ET
from zipfile import ZipFile

from service_access.access_scope import current_access, workspace_path
from storage.confined_files import copy_input


MAX_UPLOAD_BYTES = 512 * 1024 * 1024
_OPAQUE = {'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.gif', '.tif', '.tiff',
           '.exr', '.hdr', '.npy', '.npz', '.bin', '.ply', '.stl'}


def _relative(value, *, embedded=False):
    if embedded and isinstance(value, str) and value.startswith('data:'):
        return
    if (not isinstance(value, str) or not value or value.startswith(('~', '/', '\\'))
            or any(char in value for char in ('\\', ':', '%', '$', '`', '\x00', '[', ']'))
            or any(ord(char) < 32 for char in value)
            or '..' in PurePosixPath(value).parts):
        raise PermissionError('Asset dependencies must use plain relative paths inside their uploaded asset')
    return value


def _json_references(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if key == 'uri':
                _relative(child, embedded=True)
            else:
                _json_references(child)
    elif isinstance(value, list):
        for child in value:
            _json_references(child)


def _glb(path):
    with path.open('rb') as stream:
        header = stream.read(12)
        if len(header) != 12:
            raise ValueError('Invalid GLB header')
        magic, version, length = struct.unpack('<4sII', header)
        if magic != b'glTF' or version != 2 or length != path.stat().st_size:
            raise ValueError('Invalid GLB file')
        first = True
        while stream.tell() < length:
            header = stream.read(8)
            if len(header) != 8:
                raise ValueError('Invalid GLB chunk')
            size, kind = struct.unpack('<II', header)
            if size > length - stream.tell() or size % 4:
                raise ValueError('Invalid GLB chunk size')
            if first and kind != 0x4E4F534A:
                raise ValueError('GLB must begin with its JSON document')
            if kind == 0x4E4F534A:
                _json_references(json.loads(stream.read(size).decode('utf-8')))
            elif kind == 0x004E4942:
                stream.seek(size, 1)
            else:
                raise ValueError('Unknown GLB chunk cannot be dependency-validated')
            first = False
        if first:
            raise ValueError('GLB has no JSON document')


def _xml(path, suffix):
    raw = path.read_bytes()
    if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
        raise ValueError('Robot assets cannot declare XML entities or document types')
    document = ET.fromstring(raw)
    if document.tag != ('robot' if suffix == '.urdf' else 'mujoco'):
        raise ValueError('Expected an inert URDF or MJCF document')
    for element in document.iter():
        for key, value in element.attrib.items():
            if key in {'file', 'filename', 'uri', 'meshdir', 'texturedir', 'assetdir'} and value:
                _relative(value)


def _wavefront(path, suffix):
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        parts = shlex.split(line, comments=True)
        if not parts:
            continue
        key = parts[0].lower()
        if key in {'call', 'csh'}:
            raise PermissionError('Wavefront command/include directives are not supported')
        if suffix == '.obj' and key == 'mtllib':
            if '\\' in line:
                raise PermissionError('Wavefront references must use plain portable paths')
            for name in parts[1:]:
                _relative(name)
        elif suffix == '.mtl' and (key.startswith('map_') or key in {'bump', 'disp', 'decal', 'refl', 'norm'}):
            if '\\' in line:
                raise PermissionError('Wavefront references must use plain portable paths')
            if len(parts) < 2:
                raise ValueError('Material texture needs its relative filename')
            # Option values cannot smuggle an alternate filename into a parser.
            for name in parts[1:]:
                _relative(name)


def _usd(path):
    try:
        from pxr import Sdf
    except ImportError as exc:
        raise RuntimeError('USD upload validation requires the installed pxr.Sdf parser') from exc
    # This parses only one layer. Usd.Stage.Open/ComputeAllDependencies would
    # already follow untrusted references before their authorization check.
    layer = Sdf.Layer.OpenAsAnonymous(str(path))
    if layer is None:
        raise ValueError('Uploaded USD layer cannot be parsed')

    def value(item):
        if isinstance(item, Sdf.AssetPath):
            if item.path:
                _relative(item.path)
        elif isinstance(item, (Sdf.Reference, Sdf.Payload)):
            if item.assetPath:
                _relative(item.assetPath)
        elif isinstance(item, dict):
            for child in item.values():
                value(child)
        elif isinstance(item, (list, tuple, Sdf.AssetPathArray)):
            for child in item:
                value(child)
        elif isinstance(item, (Sdf.ReferenceListOp, Sdf.PayloadListOp)):
            for name in ('explicitItems', 'addedItems', 'prependedItems', 'appendedItems', 'deletedItems', 'orderedItems'):
                for child in getattr(item, name):
                    value(child)

    def inspect(spec_path):
        spec = layer.GetObjectAtPath(spec_path)
        if spec is None:
            # Sdf traversal includes relationship/connection targets, which
            # have no independent Python Spec. Their owning property is
            # visited separately; targets are USD scene paths, not assets.
            if spec_path.IsTargetPath():
                return
            raise ValueError('Uploaded USD layer contains an unreadable specification')
        for key in spec.ListInfoKeys():
            if key == 'subLayerOffsets':
                continue  # Numeric offsets have no Python conversion on some USD builds.
            info = spec.GetInfo(key)
            if key == 'subLayers':
                for name in info:
                    _relative(name)
            else:
                value(info)
    layer.Traverse('/', inspect)


def _usdz(path):
    with ZipFile(path) as archive, TemporaryDirectory(prefix='benchforge-usdz-') as directory:
        members = archive.infolist()
        if len(members) > 4096 or sum(info.file_size for info in members) > MAX_UPLOAD_BYTES:
            raise ValueError('USDZ dependency bundle exceeds its supported size')
        names = set()
        for info in members:
            _relative(info.filename)
            mode = info.external_attr >> 16
            if info.filename in names or stat.S_ISLNK(mode) or info.flag_bits & 1:
                raise ValueError('USDZ members must be unique unencrypted regular files')
            names.add(info.filename)
            if info.is_dir():
                continue
            suffix = PurePosixPath(info.filename).suffix.lower()
            if suffix == '.usdz':
                raise ValueError('Nested USDZ archives require flattening before upload')
            member = Path(directory) / info.filename
            member.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as stream, member.open('xb') as output:
                remaining = info.file_size
                while data := stream.read(min(1024 * 1024, remaining + 1)):
                    remaining -= len(data)
                    if remaining < 0:
                        raise ValueError('USDZ member exceeds its declared size')
                    output.write(data)
            _validate(member, suffix)


def _validate(path, suffix):
    if suffix in {'.usd', '.usda', '.usdc'}:
        _usd(path)
    elif suffix == '.usdz':
        _usdz(path)
    elif suffix in {'.urdf', '.xml', '.mjcf'}:
        _xml(path, suffix)
    elif suffix == '.gltf':
        _json_references(json.loads(path.read_text(encoding='utf-8-sig')))
    elif suffix == '.glb':
        _glb(path)
    elif suffix in {'.obj', '.mtl'}:
        _wavefront(path, suffix)
    elif suffix not in _OPAQUE:
        raise ValueError('This asset format has no safe dependency parser; convert it to a supported format')


def validate_upload(path, relative_path=None):
    """Validate the exact immutable bytes and name that the server will receive."""
    path = Path(path)
    name = _relative(relative_path or path.name)
    if not path.is_file() or path.stat().st_size > MAX_UPLOAD_BYTES:
        raise ValueError('Asset upload must be a bounded regular file')
    _validate(path, PurePosixPath(name).suffix.lower())


@contextmanager
def snapshot_upload(path, relative_path=None):
    """Yield a private validated snapshot; never expose its path to task code."""
    access = current_access()
    if access is None:
        yield Path(path)
        return
    path = workspace_path(path)
    name = _relative(relative_path or path.name)
    with TemporaryDirectory(prefix='benchforge-upload-') as directory:
        if Path(directory).resolve().is_relative_to(Path(access.root_path).resolve()):
            raise RuntimeError('Upload staging must be outside the model workspace')
        snapshot = Path(directory) / ('upload' + PurePosixPath(name).suffix)
        copy_input(access.root_path, path, snapshot, limit=MAX_UPLOAD_BYTES)
        validate_upload(snapshot, name)
        yield snapshot
