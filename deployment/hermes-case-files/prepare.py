"""Apply the reviewed bundle to an isolated Hermes build tree, never live files."""
import argparse
from pathlib import Path
import shutil
import subprocess


def prepare(root):
    bundle = Path(__file__).resolve().parent
    root = Path(root).resolve()
    # Patch validation precedes every write. Drift is a failed build, not a fuzzy merge.
    subprocess.run(['git', 'apply', '--check', str(bundle / 'gateway.patch')], cwd=root, check=True)
    subprocess.run(['git', 'apply', str(bundle / 'gateway.patch')], cwd=root, check=True)
    shutil.copyfile(bundle / 'supportportal_file_context.py', root / 'hermes-agent/supportportal_file_context.py')
    shutil.copyfile(bundle / 'case_files_bridge.py', root / 'deploy-ecs/supportportal_agent_tools/case_files_bridge.py')
    target = root / 'deploy-ecs/case-files-skill'
    shutil.copytree(bundle / 'skills/support-case-files', target, dirs_exist_ok=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('isolated_build_tree')
    prepare(parser.parse_args().isolated_build_tree)
