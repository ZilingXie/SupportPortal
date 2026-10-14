"""Stage only a reviewed Preproduction support profile snapshot for init/drop."""
import argparse
from pathlib import Path
import shutil
import yaml


def stage_profile(snapshot, output, effective_toolsets):
    config = yaml.safe_load(Path(snapshot).read_text()) or {}
    if not isinstance(effective_toolsets, list) or not effective_toolsets:
        raise ValueError('provide the read-back effective api_server toolsets')
    platform = config.setdefault('platform_toolsets', {})
    current = platform.get('api_server')
    tools = list(current) if isinstance(current, list) else list(effective_toolsets)
    if 'supportportal_files' not in tools:
        tools.append('supportportal_files')
    platform['api_server'] = tools
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    (output / 'config.yaml').write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False))
    shutil.copytree(Path(__file__).resolve().parent / 'skills/support-case-files', output / 'skills/support-case-files', dirs_exist_ok=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('readback_config_snapshot')
    parser.add_argument('isolated_output')
    parser.add_argument('--effective-toolsets', nargs='+', required=True)
    args = parser.parse_args()
    stage_profile(args.readback_config_snapshot, args.isolated_output, args.effective_toolsets)
