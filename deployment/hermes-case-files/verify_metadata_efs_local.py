"""Local-only Podman launcher for the real Hermes loader regression.

Starts the existing VM if needed. Does not build/pull/push images, login to a
registry, call AWS, or mount real EFS. The supplied image ID must match the
reviewed immutable release record before any container is started.
"""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import subprocess


def verify(image, expected_image_id, output):
    root = Path(__file__).resolve().parents[2]
    bundle = root / 'deployment/hermes-case-files'
    fixture = root / 'backend/tests/fixtures/hermes_case_files_old_skill.md'
    output.mkdir(parents=True, exist_ok=True)
    log_path = output / 'container-verification.log'
    result_path = output / 'container-verification.json'
    if log_path.exists() or result_path.exists():
        raise RuntimeError('output already contains an attempt; use a fresh directory')
    result = {'generated_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'network': 'none', 'image': image,
              'source_sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in
                                [bundle / 'publish_skill.py', bundle / 'probe_metadata.py',
                                 bundle / 'verify_metadata_efs.py', Path(__file__).resolve()]}}
    with log_path.open('w') as log:
        # Keep VM startup and the run in one process/tool invocation.
        subprocess.run(['podman', 'machine', 'start', 'podman-machine-default'], stdout=log, stderr=log)
        inspected = subprocess.run(['podman', 'image', 'inspect', image], capture_output=True, text=True, timeout=30)
        if inspected.returncode:
            log.write(inspected.stdout + inspected.stderr)
            raise RuntimeError('required local image unavailable; no automatic pull or login')
        info = json.loads(inspected.stdout)[0]
        if info['Id'] != expected_image_id:
            raise RuntimeError('local image ID differs from reviewed release')
        result.update(image_id=info['Id'], repo_digests=info.get('RepoDigests', []),
                      image_source=info.get('Config', {}).get('Labels', {}).get('org.opencontainers.image.revision'))
        cmd = ['podman', 'run', '--rm', '--pull', 'never', '--arch', 'amd64', '--network', 'none', '--entrypoint', 'python',
               '--volume', str(bundle) + ':/verification:ro',
               '--volume', str(fixture) + ':/old-skill.md:ro', info['Id'],
               '/verification/verify_metadata_efs.py', '--old-fixture', '/old-skill.md']
        run = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        log.write(run.stdout + '\n' + run.stderr)
        result.update(exit_code=run.returncode, command=cmd)
        if run.returncode == 0:
            markers = [line.split('METADATA_EFS_CONTAINER_JSON=', 1)[1] for line in run.stdout.splitlines()
                       if line.startswith('METADATA_EFS_CONTAINER_JSON=')]
            if len(markers) != 1:
                raise RuntimeError('missing or ambiguous container success marker')
            result['verification'] = json.loads(markers[0])
    result_path.write_text(json.dumps(result, indent=2) + '\n')
    if result['exit_code']:
        raise RuntimeError('container verification failed; inspect preserved log')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--expected-image-id', required=True)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    result = verify(args.image, args.expected_image_id, args.output)
    print('METADATA_EFS_LOCAL_PASSED=' + json.dumps({
        'image_id': result['image_id'], 'source_sha256': result['source_sha256'],
        'faults': [result['verification'][k] for k in
                   ['old_snapshot_fault', 'unreviewed_content_fault', 'wrong_path_fault']],
        'rollback_state': result['verification']['rollback_state']}))
