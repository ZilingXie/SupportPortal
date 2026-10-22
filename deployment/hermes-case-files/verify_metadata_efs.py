"""Run inside the approved image with --network none; no real EFS or AWS calls.

Mount this directory and the versioned old Skill fixture read-only. Every
publication below is confined to a TemporaryDirectory in this container.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import tempfile


def module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def verify(bundle, old_fixture, root):
    with tempfile.TemporaryDirectory(prefix='hermes-metadata-efs-v4-') as directory:
        home = Path(directory)
        # Local-only fixture for the normal, unmodified Hermes Skill loader.
        os.environ['HERMES_HOME'] = str(home)
        publisher = module(bundle / 'publish_skill.py', 'metadata_efs_publisher')
        probe = module(bundle / 'probe_metadata.py', 'metadata_efs_probe')
        layout = publisher.Layout(home, uid=os.geteuid(), gid=os.getegid())
        layout.target.parent.mkdir(parents=True)
        old = old_fixture.read_bytes()
        assert publisher.sha(old) == publisher.OLD_SHA256, 'wrong_old_fixture'
        layout.target.write_bytes(old)
        layout.target.chmod(0o644)
        new = (root / 'case-files-skill/SKILL.md').read_bytes()
        assert publisher.sha(new) == publisher.NEW_SHA256, 'wrong_image_asset'
        def check():
            return probe.probe(root, require_description=True,
                               expected_served_path=layout.target,
                               expected_served_sha256=publisher.NEW_SHA256)
        def expect_stale():
            try:
                check()
            except AssertionError as exc:
                assert str(exc) == 'actual_served_skill_metadata_missing', str(exc)
                return str(exc)
            raise AssertionError('old EFS snapshot was incorrectly accepted')
        failure = expect_stale()
        assert not layout.records.exists(), 'probe mutated publication records'
        # Metadata words alone are insufficient: reject unreviewed served bytes.
        layout.target.write_bytes(new + b'\n')
        try:
            check()
        except AssertionError as exc:
            assert str(exc) == 'actual_served_skill_hash_mismatch', str(exc)
            hash_fault = str(exc)
        else:
            raise AssertionError('unreviewed served bytes were accepted')
        layout.target.write_bytes(old)
        baseline = publisher.publish(layout, 'container-v4', publisher.OLD_SHA256, publisher.NEW_SHA256)
        assert baseline['state'] == 'baseline' and not layout.records.exists()
        applied = publisher.publish(layout, 'container-v4', publisher.OLD_SHA256,
                                    publisher.NEW_SHA256, action='apply', candidate=new)
        result = check()  # Original probe input; no loader stub or reconstruction.
        try:
            probe.probe(root, require_description=True, expected_served_path=home / 'wrong-path')
        except AssertionError as exc:
            assert str(exc) == 'actual_served_skill_path_mismatch', str(exc)
            path_fault = str(exc)
        else:
            raise AssertionError('wrong served source path was accepted')
        before_inode = layout.target.stat().st_ino
        repeated = publisher.publish(layout, 'container-v4', publisher.OLD_SHA256,
                                     publisher.NEW_SHA256, action='apply', candidate=new)
        assert repeated['changed'] is False and layout.target.stat().st_ino == before_inode
        restored = publisher.publish(layout, 'container-v4', publisher.OLD_SHA256,
                                     publisher.NEW_SHA256, action='rollback')
        assert restored['state'] == 'rolled_back' and layout.target.read_bytes() == old
        assert expect_stale() == failure
        return {'old_snapshot_fault': failure, 'apply_state': applied['state'],
                'unreviewed_content_fault': hash_fault, 'wrong_path_fault': path_fault,
                'probe': result, 'repeat_changed': repeated['changed'],
                'rollback_state': restored['state'], 'rollback_bytes_equal': True,
                'fixture_sha256': publisher.sha(old), 'source_sha256': publisher.sha(new),
                'backend_tools_called': False, 'sessions_created': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--old-fixture', required=True, type=Path)
    parser.add_argument('--root', type=Path, default=Path('/opt/hermes'))
    args = parser.parse_args()
    print('METADATA_EFS_CONTAINER_JSON=' + json.dumps(
        verify(Path(__file__).resolve().parent, args.old_fixture, args.root), sort_keys=True))
