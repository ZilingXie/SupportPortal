"""Controlled single-Skill publication; production CLI is Preproduction-only.

No AWS writes or model tools. The CLI verifies the current ECS task via the
link-local metadata endpoint, then operates only on the fixed EFS Skill.
check-only never creates a directory, lock, backup, temporary file or record.
"""
import argparse
from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import urllib.parse
import urllib.request

OLD_SHA256 = '7379499fc8412b4a52cd5ad82a31f74ae580ae02c8577ef4eb2a7c16910ebc02'
NEW_SHA256 = 'bce133eb503042e75f67b7d512877fa4a2efccc2af75766c2fe15a00b330154a'
BUNDLED_SKILL = Path('/opt/hermes/case-files-skill/SKILL.md')
STATES = {'prepared', 'committed', 'outcome_unknown', 'rolled_back', 'ownership_conflict'}


class PublicationError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def require(condition, code):
    if not condition:
        raise PublicationError(code)


@dataclass(frozen=True)
class Layout:
    data_root: Path
    uid: int = 10000
    gid: int = 10000
    mode: int = 0o644

    @property
    def target(self):
        return self.data_root / 'skills/support-case-files/SKILL.md'

    @property
    def records(self):
        return self.data_root / '.supportportal-skill-releases/support-case-files'


PRODUCTION_LAYOUT = Layout(Path('/opt/data'))


def sha(data):
    return hashlib.sha256(data).hexdigest()


def safe_path(path, root):
    require(root.is_absolute() and root.resolve() == root, 'unsafe_root')
    require(path.is_relative_to(root), 'path_escape')
    current = root
    require(not current.is_symlink(), 'symlink_path')
    for part in path.relative_to(root).parts:
        current = current / part
        require(not current.is_symlink(), 'symlink_path')
        if current.exists() and current != path:
            require(current.is_dir(), 'non_directory_parent')


def snapshot(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as file:
        info = os.fstat(file.fileno())
        require(stat.S_ISREG(info.st_mode), 'not_regular_file')
        data = file.read()
    return data, {'sha256': sha(data), 'uid': info.st_uid, 'gid': info.st_gid,
                  'mode': stat.S_IMODE(info.st_mode), 'size': info.st_size,
                  'device': info.st_dev, 'inode': info.st_ino,
                  'mtime_ns': info.st_mtime_ns}


def target_snapshot(layout):
    safe_path(layout.target, layout.data_root)
    data, info = snapshot(layout.target)
    require((info['uid'], info['gid'], info['mode']) ==
            (layout.uid, layout.gid, layout.mode), 'target_attributes_changed')
    require(info['device'] == layout.data_root.stat().st_dev, 'different_filesystem')
    return data, info


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write(path, data, *, uid, gid, mode, before_replace=None, exclusive=False):
    """Commit once by rename; any raised error may include an unknown commit."""
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as file:
            file.write(data)
            os.fchmod(file.fileno(), mode)
            info = os.fstat(file.fileno())
            if (info.st_uid, info.st_gid) != (uid, gid):
                os.fchown(file.fileno(), uid, gid)
            file.flush()
            os.fsync(file.fileno())
        prepared, attributes = snapshot(Path(temporary))
        require(prepared == data, 'temporary_content_mismatch')
        require((attributes['uid'], attributes['gid'], attributes['mode']) ==
                (uid, gid, mode), 'temporary_attributes_mismatch')
        if before_replace is not None:
            before_replace()
        if exclusive:
            os.link(temporary, path)  # Never replace a pre-existing backup.
        else:
            os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def record_write(path, record):
    # EFS access points can enforce a POSIX identity different from the process
    # euid. Preserve the directory's actual filesystem identity, not caller euid.
    parent = path.parent.stat()
    atomic_write(path, (json.dumps(record, sort_keys=True) + '\n').encode(),
                 uid=parent.st_uid, gid=parent.st_gid, mode=0o600)


def load_record(path, release_id, old_hash, new_hash, layout):
    safe_path(path, layout.data_root)
    if not path.exists():
        return None
    record = json.loads(snapshot(path)[0])
    require(isinstance(record, dict), 'invalid_record')
    require(record.get('release_id') == release_id and
            record.get('old_sha256') == old_hash and
            record.get('new_sha256') == new_hash and
            record.get('target') == str(layout.target), 'release_input_conflict')
    require(record.get('state') in STATES, 'invalid_record_state')
    require(record.get('pending_action') in {'apply', 'rollback'}, 'invalid_record_intent')
    return record


@contextmanager
def publication_lock(layout):
    safe_path(layout.records, layout.data_root)
    layout.records.mkdir(parents=True, exist_ok=True)
    lock = layout.records / 'publish.lock'
    fd = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        require(stat.S_ISREG(os.fstat(fd).st_mode), 'invalid_lock_file')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise PublicationError('publication_busy') from exc
        yield
    finally:
        os.close(fd)


def publish(layout, release_id, old_hash, new_hash, *, action='check-only', candidate=None):
    """File transaction used by the CLI; tests supply an isolated Layout."""
    require(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,95}', release_id), 'invalid_release_id')
    require(action in {'check-only', 'apply', 'rollback'}, 'invalid_action')
    require(all(re.fullmatch(r'[0-9a-f]{64}', h) for h in (old_hash, new_hash))
            and old_hash != new_hash, 'invalid_hashes')
    safe_path(layout.records, layout.data_root)
    operation = layout.records / release_id
    record_path = operation / 'record.json'
    backup_path = operation / 'SKILL.md.before'
    safe_path(record_path, layout.data_root)
    safe_path(backup_path, layout.data_root)
    _, current = target_snapshot(layout)
    record = load_record(record_path, release_id, old_hash, new_hash, layout)
    require(current['sha256'] in {old_hash, new_hash}, 'ownership_conflict')
    if action == 'check-only':
        if record:
            require(snapshot(backup_path)[1]['sha256'] == old_hash, 'backup_mismatch')
            require(record['state'] != 'ownership_conflict', 'ownership_conflict')
            if record['state'] in {'committed', 'rolled_back'}:
                require(current['sha256'] == (new_hash if record['state'] == 'committed' else old_hash),
                        'state_conflict')
        else:
            require(current['sha256'] == old_hash, 'unowned_new_content')
        return {'ok': True, 'state': record['state'] if record else 'baseline',
                'action': action, 'release_id': release_id, 'current': current}
    if action == 'apply':
        require(isinstance(candidate, bytes) and sha(candidate) == new_hash, 'candidate_hash_mismatch')
    if action == 'rollback' and record is None and current['sha256'] == old_hash:
        return {'ok': True, 'state': 'baseline', 'action': action, 'changed': False, 'current': current}
    with publication_lock(layout):
        data, current = target_snapshot(layout)
        record = load_record(record_path, release_id, old_hash, new_hash, layout)
        require(current['sha256'] in {old_hash, new_hash}, 'ownership_conflict')
        if record is None:
            require(action == 'apply' and current['sha256'] == old_hash, 'unowned_new_content')
            safe_path(operation, layout.data_root)
            operation.mkdir(exist_ok=True)
            safe_path(backup_path, layout.data_root)
            if not backup_path.exists():
                atomic_write(backup_path, data, uid=layout.uid, gid=layout.gid,
                             mode=0o600, exclusive=True)
            require(snapshot(backup_path)[0] == data, 'backup_mismatch')
            record = {'release_id': release_id, 'target': str(layout.target),
                      'old_sha256': old_hash, 'new_sha256': new_hash,
                      'before': current, 'state': 'prepared', 'pending_action': 'apply'}
            record_write(record_path, record)
        backup, backup_info = snapshot(backup_path)
        require(backup_info['sha256'] == old_hash, 'backup_mismatch')
        before = record['before']
        require((before['sha256'], before['uid'], before['gid'], before['mode']) ==
                (old_hash, layout.uid, layout.gid, layout.mode), 'backup_attributes_mismatch')
        if record['state'] == 'ownership_conflict':
            raise PublicationError('ownership_conflict')
        if action == 'apply' and record['state'] == 'rolled_back':
            raise PublicationError('release_already_rolled_back')
        if action == 'apply' and record.get('pending_action') == 'rollback':
            raise PublicationError('rollback_requires_readback')
        if action == 'apply' and current['sha256'] == new_hash:
            require(record['state'] in {'prepared', 'outcome_unknown', 'committed'}, 'state_conflict')
            if record['state'] != 'committed':
                record.update(state='committed', after=current)
                try:
                    record_write(record_path, record)
                except OSError as exc:
                    raise PublicationError('outcome_unknown') from exc
            return {'ok': True, 'state': 'committed', 'changed': False, 'current': current}
        if action == 'rollback' and current['sha256'] == old_hash:
            record.update(state='rolled_back')
            record_write(record_path, record)
            return {'ok': True, 'state': 'rolled_back', 'changed': False, 'current': current}
        if action == 'apply':
            require(record['state'] in {'prepared', 'outcome_unknown'}, 'state_conflict')
            require(current == before, 'ownership_conflict')
            payload = candidate
        else:
            require(record['state'] in {'prepared', 'outcome_unknown', 'committed'}, 'state_conflict')
            payload = backup
            record.update(state='prepared', pending_action='rollback')
            record_write(record_path, record)
        # The lock coordinates participating publishers only. Recheck the exact
        # observed target immediately before rename; never overwrite known drift.
        expected = current
        def recheck():
            require(target_snapshot(layout)[1] == expected, 'ownership_conflict')
        try:
            atomic_write(layout.target, payload, uid=layout.uid, gid=layout.gid,
                         mode=layout.mode, before_replace=recheck)
            _, observed = target_snapshot(layout)
            if observed['sha256'] != sha(payload):
                record.update(state='ownership_conflict')
                record_write(record_path, record)
                raise PublicationError('ownership_conflict')
            record.update(state='committed' if action == 'apply' else 'rolled_back', after=observed)
            record_write(record_path, record)
        except Exception as exc:
            if isinstance(exc, PublicationError) and exc.code == 'ownership_conflict':
                record['state'] = 'ownership_conflict'
            elif record['state'] != 'ownership_conflict':
                record['state'] = 'outcome_unknown'
            try:
                record_write(record_path, record)
            except OSError:
                pass  # prepared record + actual target remain the recovery source.
            if isinstance(exc, PublicationError):
                raise
            raise PublicationError('outcome_unknown') from exc
        return {'ok': True, 'state': record['state'], 'changed': True, 'current': observed}


def verify_preproduction_task(expected_task_arn):
    endpoint = os.environ.get('ECS_CONTAINER_METADATA_URI_V4', '')
    parsed = urllib.parse.urlparse(endpoint)
    require(parsed.scheme == 'http' and parsed.hostname == '169.254.170.2'
            and parsed.port in {None, 80} and not parsed.query and not parsed.fragment,
            'ecs_metadata_endpoint_required')
    with urllib.request.urlopen(endpoint.rstrip('/') + '/task', timeout=5) as response:
        task = json.load(response)
    require(task.get('Family') == 'supportportal-preproduction-hermes'
            and task.get('TaskARN') == expected_task_arn
            and re.fullmatch(r'arn:aws:ecs:us-east-1:891612554546:task/supportportal-preproduction/[a-f0-9]{32}',
                             expected_task_arn or '')
            and str(task.get('Cluster', '')).rsplit('/', 1)[-1] == 'supportportal-preproduction',
            'preproduction_task_identity_mismatch')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--action', choices=['check-only', 'apply', 'rollback'], default='check-only')
    parser.add_argument('--release-id', required=True)
    parser.add_argument('--expected-old-sha256', required=True)
    parser.add_argument('--expected-new-sha256', required=True)
    parser.add_argument('--expected-task-arn', required=True)
    args = parser.parse_args(argv)
    try:
        verify_preproduction_task(args.expected_task_arn)
        require((args.expected_old_sha256, args.expected_new_sha256) == (OLD_SHA256, NEW_SHA256),
                'unreviewed_payload_hashes')
        candidate = snapshot(BUNDLED_SKILL)[0] if args.action == 'apply' else None
        result = publish(PRODUCTION_LAYOUT, args.release_id, args.expected_old_sha256,
                         args.expected_new_sha256, action=args.action, candidate=candidate)
    except (PublicationError, OSError, ValueError, KeyError) as exc:
        result = {'ok': False, 'code': exc.code if isinstance(exc, PublicationError) else type(exc).__name__}
    print('SKILL_PUBLICATION_JSON=' + json.dumps(result, sort_keys=True))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
