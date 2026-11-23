"""Keep checkout-based DSH bundles current before launching their preset."""
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
from urllib.parse import urlsplit
from urllib.request import url2pathname

from storage.file_lock import file_lock


_STAGED_OUTPUTS = (
    'dist/resources/assessment_template.json',
    'dist/resources/task_templates.json',
    'dist/resources/tool-names.json',
    'dist/resources/production-stages.json',
    'dist/task_support/README.md',
    'dist/task_support/public_measurements.py',
    'dist/task_support/public_sensor_cache.py',
    'dist/task_support/public_geometry_cache.py',
)


def local_bundle_entries(profile, preset, dsh):
    """Find enabled plugin entries that load this checkout's build products."""
    if isinstance(profile, list):
        return set().union(*(local_bundle_entries(row, preset, dsh) for row in profile))
    if not isinstance(profile, dict) or str(profile.get('disabled')).lower() == 'true':
        return set()

    entries = set()
    name = profile.get('name')
    if isinstance(name, str):
        path = None
        if name.lower().startswith('file:'):
            url = urlsplit(name)
            location = ('//' + url.netloc if url.netloc and url.netloc != 'localhost' else '') + url.path
            path = Path(url2pathname(location)).resolve()
        elif name.startswith(('./', '../', '.\\', '..\\')) or Path(name).is_absolute():
            path = (preset.parent / name).resolve()
        if path is not None and (path.is_relative_to(dsh / 'dist') or path == dsh / 'client/index.js'):
            entries.add(path)
    for value in profile.values():
        entries.update(local_bundle_entries(value, preset, dsh))
    return entries


def _digest_file(path):
    digest = sha256()
    with path.open('rb') as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _source_digest(dsh):
    # Cover transitive JS/TS/CSS/JSON inputs as well as resources copied into dist.
    # Generated remote declarations, client.js and dist come from these inputs.
    source = dsh.parent
    paths = []
    for directory, children, files in os.walk(source):
        relative = Path(directory).relative_to(source)
        children[:] = [name for name in children if name not in {'__pycache__', 'node_modules', 'dist', '.git'}]
        if relative == Path('dsh/remote') and 'lib' in children:
            children.remove('lib')
        for name in files:
            path = relative / name
            if path == Path('dsh/client/client.js') or '.test.' in name:
                continue
            if path.suffix in {'.mjs', '.js', '.cjs', '.ts', '.tsx', '.jsx', '.css', '.json'}:
                paths.append(path)
    paths.extend(Path('capabilities/data_acquisition/measurements') / Path(output).name
                 for output in _STAGED_OUTPUTS if output.startswith('dist/task_support/'))
    inputs = {path.as_posix(): _digest_file(source / path) for path in sorted(paths)}
    return sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()


def _output_digests(dsh):
    manifest = json.loads((dsh / 'package.json').read_text(encoding='utf-8'))
    paths = {dsh / target for target in manifest['exports'].values() if target.startswith('./dist/')}
    paths.update({dsh / 'dist/workbench.mjs', dsh / 'client/client.js'})
    paths.update(dsh / target for target in _STAGED_OUTPUTS)
    outputs = {}
    for path in sorted(paths):
        if not path.is_file() or path.stat().st_size == 0:
            return None
        outputs[path.relative_to(dsh).as_posix()] = _digest_file(path)
    return outputs


def ensure_local_bundles(dsh, node, entries):
    """Serialize startup builds and record success only after outputs are ready."""
    state = dsh.parents[1] / '.benchforge'
    stamp = state / 'dsh-bundles.json'
    # All launchers use the same source checkout and therefore the same lock.
    with file_lock(state / 'dsh-build.lock'):
        source = _source_digest(dsh)
        try:
            previous = json.loads(stamp.read_text(encoding='utf-8'))
        except (FileNotFoundError, ValueError):
            previous = None
        outputs = _output_digests(dsh)
        if outputs and previous == {'source': source, 'outputs': outputs} and all(
            path.is_file() for path in entries
        ):
            return

        stamp.unlink(missing_ok=True)
        print('Building BenchForge DSH bundles from this checkout.', flush=True)
        try:
            subprocess.run([node, str(dsh / 'build.mjs')], cwd=dsh, check=True)
        except FileNotFoundError as error:
            raise RuntimeError(f'Cannot start the DSH build with Node executable {node}.') from error
        except subprocess.CalledProcessError as error:
            raise RuntimeError(
                f'DSH bundle build failed (exit {error.returncode}). '
                'See the build output above. To restore the locked dependencies, run npm ci --prefix src/dsh.'
            ) from error

        outputs = _output_digests(dsh)
        if not outputs:
            raise RuntimeError('The DSH build did not produce all required bundle files.')
        for path in sorted(entries):
            if not path.is_file():
                raise FileNotFoundError(f'DSH bundle referenced by the preset does not exist: {path}')
        if _source_digest(dsh) != source:
            raise RuntimeError('BenchForge DSH source changed during the build. Restart to rebuild the latest source.')
        staged = stamp.with_suffix('.tmp')
        staged.write_text(json.dumps({'source': source, 'outputs': outputs}), encoding='utf-8')
        staged.replace(stamp)
