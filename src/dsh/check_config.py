"""Check a DSH preset and prepare the local bundles it loads before startup."""
import argparse
import json
from pathlib import Path
import sys

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from service_access.scene_service import resolve_service_config_file


# 递归查找启用的 BenchForge 工具所引用的服务配置，跳过禁用插件。
def service_config_paths(value):
    if isinstance(value, list):
        for child in value:
            yield from service_config_paths(child)
    elif isinstance(value, dict):
        if value.get('disabled') == 'true':
            return
        config = value.get('config')
        if value.get('id') in {'benchforge-scene-tools', 'spatialforge-tools'} and isinstance(config, dict):
            if 'serviceConfig' in config:
                yield config['serviceConfig']
        for child in value.values():
            yield from service_config_paths(child)


# 以不执行自定义标签的 YAML 读取器解析预设，并去重校验引用服务配置。
def check_preset(preset, profile=None):
    if profile is None:
        # BaseLoader reads tagged values such as !!js as text.
        profile = yaml.load(Path(preset).read_text(encoding='utf-8'), Loader=yaml.BaseLoader)
    checked = set()
    for value in service_config_paths(profile):
        if not isinstance(value, str) or not value.strip():
            raise ValueError('benchforge-scene-tools.serviceConfig must name a configuration file')
        path = resolve_service_config_file(value)
        if path in checked:
            continue
        if not path.is_file():
            raise FileNotFoundError(
                f'BenchForge configuration referenced by {preset} does not exist: {path}. '
                'Run python benchforge_app.py init to create the default client configuration. '
                'If the configuration was moved, update serviceConfig in this preset to its actual path '
                '(for example config/generated/benchforge-client.local.json).')
        config = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(config, dict):
            raise ValueError(f'BenchForge configuration must be a JSON object: {path}')
        checked.add(path)
    return checked


# 解析待检查预设路径，报告配置错误或已校验的服务配置数量。
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preset', required=True, type=Path)
    parser.add_argument('--node', default='node', help='Node executable used to launch DSH')
    parser.add_argument('--config-only', action='store_true', help='Validate service configuration without building')
    args = parser.parse_args()
    try:
        profile = yaml.load(args.preset.read_text(encoding='utf-8'), Loader=yaml.BaseLoader)
        checked = check_preset(args.preset, profile)
        dsh = Path(__file__).resolve().parent
        if not args.config_only and (dsh / 'build.mjs').is_file():
            from dsh.local_bundles import ensure_local_bundles, local_bundle_entries

            entries = local_bundle_entries(profile, args.preset, dsh)
            if entries:
                ensure_local_bundles(dsh, args.node, entries)
    except (OSError, RuntimeError, ValueError, yaml.YAMLError) as error:
        parser.error(str(error))
    print(f'Checked {len(checked)} BenchForge configuration file(s).')


if __name__ == '__main__':
    main()
