"""Optional interactive RGB preview of the settled scene, outside capture evidence."""
import logging
from pathlib import Path
import re
import struct
import tempfile
import threading

from capabilities.isaac_sim.evidence import resolve_artifact
from capabilities.isaac_sim.grpc.tasks import DurableTasks
from capabilities.isaac_sim.grpc.transport import A100Client

PREVIEW_NAME = re.compile(r'preview/attempt_(?:0|[1-9][0-9]*)/first_view\.png')
_slots = threading.BoundedSemaphore(16)


def preview_name(generation):
    return f'preview/attempt_{int(generation)}/first_view.png'


def public_preview_file(folder, generation):
    name = preview_name(generation)
    path = folder / name
    if (not folder.is_symlink() and not path.parent.parent.is_symlink() and not path.parent.is_symlink()
            and not path.is_symlink() and path.is_file() and path.stat().st_size > 0):
        return path


def current_preview_file(folder):
    attempts = [int(p.name[8:]) for p in (folder / 'isaac_sim').glob('attempt_*')
                if re.fullmatch(r'attempt_(?:0|[1-9][0-9]*)', p.name) and p.is_dir()]
    if attempts:
        return public_preview_file(folder, max(attempts))


def fetch_preview(config, settings, identity, camera, directory, generation):
    size = settings.get('size', 1024)
    data = {'session_id': identity, 'camera': camera, 'size': size, 'spp': settings.get('spp', 1)}
    attempt = directory / 'isaac_sim' / f'attempt_{generation}'
    client = A100Client(config)
    try:
        jobs = DurableTasks(client, attempt / 'jobs', str(attempt))
        job, result = jobs.extension('preview', 'scene.preview', data)
        if not result.get('scene_version'):
            raise ValueError('Preview returned no scene version')
        source = resolve_artifact(result['image'], jobs.download('preview', job))
        with source.open('rb') as handle:
            image = handle.read(16 * 1024 * 1024 + 1)
    finally:
        client.close()
    if (len(image) > 16 * 1024 * 1024 or image[:8] != b'\x89PNG\r\n\x1a\n' or
            len(image) < 33 or struct.unpack('>II', image[16:24]) != (size, size) or image[-8:-4] != b'IEND'):
        raise ValueError('Incomplete or mismatched preview PNG')
    target = directory / preview_name(generation)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as handle:
            temp = Path(handle.name)
            handle.write(image)
        temp.replace(target)
    finally:
        if temp:
            temp.unlink(missing_ok=True)


def start_preview(config, settings, identity, camera, directory, generation):
    if not settings or public_preview_file(directory, generation) or not _slots.acquire(blocking=False):
        return
    def work():
        try:
            fetch_preview(config, settings, identity, camera, directory, generation)
        except Exception:
            logging.getLogger(__name__).warning('Interactive preview unavailable for %s', identity, exc_info=True)
        finally:
            _slots.release()
    try:
        threading.Thread(target=work, name='isaac-preview', daemon=True).start()
    except RuntimeError:
        _slots.release()
        logging.getLogger(__name__).warning('Interactive preview thread unavailable')
