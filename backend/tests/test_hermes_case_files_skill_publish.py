import importlib.util
import json
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / 'deployment/hermes-case-files/publish_skill.py'
OLD = '7379499fc8412b4a52cd5ad82a31f74ae580ae02c8577ef4eb2a7c16910ebc02'
NEW = 'bce133eb503042e75f67b7d512877fa4a2efccc2af75766c2fe15a00b330154a'


def load():
    spec = importlib.util.spec_from_file_location('skill_publisher_test', MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def layout(tmp_path):
    mod = load()
    root = tmp_path / 'data'
    target = root / 'skills/support-case-files/SKILL.md'
    target.parent.mkdir(parents=True)
    old = (ROOT / 'backend/tests/fixtures/hermes_case_files_old_skill.md').read_bytes()
    target.write_bytes(old)
    os.chmod(target, 0o644)
    return mod, mod.Layout(root, uid=os.getuid(), gid=os.getgid()), old


def test_check_only_is_strictly_read_only(tmp_path):
    mod, lay, old = layout(tmp_path)
    before = sorted(p.relative_to(lay.data_root).as_posix() for p in lay.data_root.rglob('*'))
    result = mod.publish(lay, 'r-check', OLD, NEW)
    after = sorted(p.relative_to(lay.data_root).as_posix() for p in lay.data_root.rglob('*'))
    assert result['state'] == 'baseline'
    assert before == after
    assert lay.target.read_bytes() == old


def test_apply_uses_real_bytes_and_records_backup(tmp_path):
    mod, lay, old = layout(tmp_path)
    new = (ROOT / 'deployment/hermes-case-files/skills/support-case-files/SKILL.md').read_bytes()
    result = mod.publish(lay, 'r-apply', OLD, NEW, action='apply', candidate=new)
    assert result['state'] == 'committed'
    assert mod.sha(lay.target.read_bytes()) == NEW
    record = json.loads((lay.records / 'r-apply/record.json').read_text())
    assert record['state'] == 'committed'
    assert (lay.records / 'r-apply/SKILL.md.before').read_bytes() == old
    assert record['before']['mode'] == 0o644


def test_repeated_apply_is_idempotent_without_replacement(tmp_path, monkeypatch):
    mod, lay, _ = layout(tmp_path)
    new = (ROOT / 'deployment/hermes-case-files/skills/support-case-files/SKILL.md').read_bytes()
    mod.publish(lay, 'r-repeat', OLD, NEW, action='apply', candidate=new)
    original = mod.atomic_write
    monkeypatch.setattr(mod, 'atomic_write', lambda *a, **k: pytest.fail('must not replace again'))
    result = mod.publish(lay, 'r-repeat', OLD, NEW, action='apply', candidate=new)
    assert result['changed'] is False and result['state'] == 'committed'
    monkeypatch.setattr(mod, 'atomic_write', original)


def test_rollback_requires_ownership_and_restores_old_bytes(tmp_path):
    mod, lay, old = layout(tmp_path)
    new = (ROOT / 'deployment/hermes-case-files/skills/support-case-files/SKILL.md').read_bytes()
    mod.publish(lay, 'r-rollback', OLD, NEW, action='apply', candidate=new)
    result = mod.publish(lay, 'r-rollback', OLD, NEW, action='rollback')
    assert result['state'] == 'rolled_back'
    assert lay.target.read_bytes() == old
    assert json.loads((lay.records / 'r-rollback/record.json').read_text())['state'] == 'rolled_back'


def test_foreign_content_fails_closed_and_never_overwrites(tmp_path):
    mod, lay, _ = layout(tmp_path)
    lay.target.write_bytes(b'foreign-content')
    with pytest.raises(mod.PublicationError, match='ownership_conflict'):
        mod.publish(lay, 'r-conflict', OLD, NEW, action='apply', candidate=b'x')
    assert lay.target.read_bytes() == b'foreign-content'
    assert not lay.records.exists()


def test_candidate_and_release_inputs_are_pinned(tmp_path):
    mod, lay, _ = layout(tmp_path)
    with pytest.raises(mod.PublicationError, match='candidate_hash_mismatch'):
        mod.publish(lay, 'r-bad', OLD, NEW, action='apply', candidate=b'wrong')
    with pytest.raises(mod.PublicationError, match='invalid_release_id'):
        mod.publish(lay, 'bad/release', OLD, NEW)


def test_rollback_unknown_result_can_be_recovered_by_readback(tmp_path, monkeypatch):
    mod, lay, _ = layout(tmp_path)
    new = (ROOT / 'deployment/hermes-case-files/skills/support-case-files/SKILL.md').read_bytes()
    original = mod.record_write
    calls = {'n': 0}
    def fail_once(path, record):
        calls['n'] += 1
        if calls['n'] == 2 and record['state'] == 'committed':
            raise OSError('record write uncertain')
        return original(path, record)
    monkeypatch.setattr(mod, 'record_write', fail_once)
    with pytest.raises(mod.PublicationError, match='outcome_unknown'):
        mod.publish(lay, 'r-unknown', OLD, NEW, action='apply', candidate=new)
    assert mod.sha(lay.target.read_bytes()) == NEW
    # The same operation reads the target and confirms committed without replacing.
    monkeypatch.setattr(mod, 'record_write', original)
    result = mod.publish(lay, 'r-unknown', OLD, NEW, action='apply', candidate=new)
    assert result['state'] == 'committed' and result['changed'] is False


@pytest.mark.parametrize('location', ['target', 'skill_directory', 'records', 'release', 'backup'])
def test_symlink_or_path_escape_is_rejected_before_writes(tmp_path, location):
    mod, lay, old = layout(tmp_path)
    outside = tmp_path / 'outside'
    outside.mkdir()
    if location == 'target':
        lay.target.unlink()
        saved = outside / 'SKILL.md'
        saved.write_bytes(old)
        lay.target.symlink_to(saved)
    elif location == 'skill_directory':
        lay.target.unlink()
        lay.target.parent.rmdir()
        (outside / 'SKILL.md').write_bytes(old)
        lay.target.parent.symlink_to(outside)
    elif location == 'records':
        lay.records.parent.mkdir()
        lay.records.symlink_to(outside)
    elif location == 'release':
        lay.records.mkdir(parents=True)
        (lay.records / 'r-link').symlink_to(outside)
    else:
        operation = lay.records / 'r-link'
        operation.mkdir(parents=True)
        (operation / 'SKILL.md.before').symlink_to(outside / 'secret')
    with pytest.raises(mod.PublicationError, match='symlink_path'):
        mod.publish(lay, 'r-link', OLD, NEW)
    assert sorted(p.name for p in outside.iterdir()) == (['SKILL.md'] if location in {'target', 'skill_directory'} else [])


def test_target_attributes_and_bad_backup_fail_closed(tmp_path):
    mod, lay, old = layout(tmp_path)
    new = (ROOT / 'deployment/hermes-case-files/skills/support-case-files/SKILL.md').read_bytes()
    os.chmod(lay.target, 0o600)
    with pytest.raises(mod.PublicationError, match='target_attributes_changed'):
        mod.publish(lay, 'r-mode', OLD, NEW, action='apply', candidate=new)
    assert not lay.records.exists()
    os.chmod(lay.target, 0o644)
    mod.publish(lay, 'r-backup', OLD, NEW, action='apply', candidate=new)
    (lay.records / 'r-backup/SKILL.md.before').write_bytes(b'foreign backup')
    with pytest.raises(mod.PublicationError, match='backup_mismatch'):
        mod.publish(lay, 'r-backup', OLD, NEW, action='rollback')
    assert lay.target.read_bytes() == new
    assert new != old


def test_backup_failure_then_same_original_payload_recovers(tmp_path, monkeypatch):
    mod, lay, old = layout(tmp_path)
    new = (ROOT / 'deployment/hermes-case-files/skills/support-case-files/SKILL.md').read_bytes()
    atomic = mod.atomic_write
    def fail_backup(path, *args, **kwargs):
        if path.name == 'SKILL.md.before':
            raise OSError('backup unavailable')
        return atomic(path, *args, **kwargs)
    monkeypatch.setattr(mod, 'atomic_write', fail_backup)
    with pytest.raises(OSError, match='backup unavailable'):
        mod.publish(lay, 'r-backup-retry', OLD, NEW, action='apply', candidate=new)
    assert lay.target.read_bytes() == old
    assert not (lay.records / 'r-backup-retry/record.json').exists()
    monkeypatch.setattr(mod, 'atomic_write', atomic)
    assert mod.publish(lay, 'r-backup-retry', OLD, NEW, action='apply', candidate=new)['state'] == 'committed'
    assert lay.target.read_bytes() == new


@pytest.mark.parametrize('fail_after_commit', [False, True])
def test_target_rename_fault_recovers_same_operation_without_reconstruction(tmp_path, monkeypatch, fail_after_commit):
    mod, lay, old = layout(tmp_path)
    new = (ROOT / 'deployment/hermes-case-files/skills/support-case-files/SKILL.md').read_bytes()
    replace = mod.os.replace
    replacements = []
    def fail_rename(src, dst):
        if Path(dst) == lay.target:
            replacements.append(str(dst))
            if fail_after_commit:
                replace(src, dst)
            raise OSError('rename outcome unknown')
        return replace(src, dst)
    monkeypatch.setattr(mod.os, 'replace', fail_rename)
    with pytest.raises(mod.PublicationError, match='outcome_unknown'):
        mod.publish(lay, 'r-rename', OLD, NEW, action='apply', candidate=new)
    assert lay.target.read_bytes() == (new if fail_after_commit else old)
    assert json.loads((lay.records / 'r-rename/record.json').read_text())['state'] == 'outcome_unknown'
    assert (lay.records / 'r-rename/SKILL.md.before').read_bytes() == old
    monkeypatch.setattr(mod.os, 'replace', replace)
    result = mod.publish(lay, 'r-rename', OLD, NEW, action='apply', candidate=new)
    assert result['changed'] is (not fail_after_commit)
    assert result['state'] == 'committed' and lay.target.read_bytes() == new


def test_third_party_edit_between_staging_and_rename_is_not_overwritten(tmp_path, monkeypatch):
    mod, lay, _ = layout(tmp_path)
    new = (ROOT / 'deployment/hermes-case-files/skills/support-case-files/SKILL.md').read_bytes()
    atomic = mod.atomic_write
    def edit_after_staging(path, data, **kwargs):
        if path == lay.target:
            original_check = kwargs['before_replace']
            def edit_and_check():
                lay.target.write_bytes(b'foreign at commit')
                original_check()
            kwargs['before_replace'] = edit_and_check
        return atomic(path, data, **kwargs)
    monkeypatch.setattr(mod, 'atomic_write', edit_after_staging)
    with pytest.raises(mod.PublicationError, match='ownership_conflict'):
        mod.publish(lay, 'r-drift', OLD, NEW, action='apply', candidate=new)
    assert lay.target.read_bytes() == b'foreign at commit'
    assert json.loads((lay.records / 'r-drift/record.json').read_text())['state'] == 'ownership_conflict'


def test_nonblocking_lock_refuses_other_process(tmp_path):
    import subprocess
    import sys
    mod, lay, old = layout(tmp_path)
    lay.records.mkdir(parents=True)
    code = 'import fcntl,sys; f=open(sys.argv[1],"w"); fcntl.flock(f,fcntl.LOCK_EX); print("LOCKED",flush=True); sys.stdin.read()'
    process = subprocess.Popen([sys.executable, '-c', code, str(lay.records / 'publish.lock')],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert process.stdout.readline().strip() == 'LOCKED'
        new = (ROOT / 'deployment/hermes-case-files/skills/support-case-files/SKILL.md').read_bytes()
        with pytest.raises(mod.PublicationError, match='publication_busy'):
            mod.publish(lay, 'r-locked', OLD, NEW, action='apply', candidate=new)
        assert lay.target.read_bytes() == old
        assert not (lay.records / 'r-locked').exists()
    finally:
        process.communicate('', timeout=5)


def test_rollback_unknown_is_reconciled_before_apply_can_reactivate(tmp_path, monkeypatch):
    mod, lay, old = layout(tmp_path)
    new = (ROOT / 'deployment/hermes-case-files/skills/support-case-files/SKILL.md').read_bytes()
    mod.publish(lay, 'r-rollback-unknown', OLD, NEW, action='apply', candidate=new)
    writer = mod.record_write
    def fail_rollback_record(path, record):
        if record['state'] == 'rolled_back':
            raise OSError('rollback response lost')
        return writer(path, record)
    monkeypatch.setattr(mod, 'record_write', fail_rollback_record)
    with pytest.raises(mod.PublicationError, match='outcome_unknown'):
        mod.publish(lay, 'r-rollback-unknown', OLD, NEW, action='rollback')
    assert lay.target.read_bytes() == old
    with pytest.raises(mod.PublicationError, match='rollback_requires_readback'):
        mod.publish(lay, 'r-rollback-unknown', OLD, NEW, action='apply', candidate=new)
    monkeypatch.setattr(mod, 'record_write', writer)
    assert mod.publish(lay, 'r-rollback-unknown', OLD, NEW, action='rollback')['state'] == 'rolled_back'
    with pytest.raises(mod.PublicationError, match='release_already_rolled_back'):
        mod.publish(lay, 'r-rollback-unknown', OLD, NEW, action='apply', candidate=new)
    assert lay.target.read_bytes() == old
    assert (lay.target.stat().st_uid, lay.target.stat().st_gid, lay.target.stat().st_mode & 0o777) == (lay.uid, lay.gid, 0o644)


def test_check_only_rejects_state_target_contradiction_without_writing(tmp_path):
    mod, lay, old = layout(tmp_path)
    new = (ROOT / 'deployment/hermes-case-files/skills/support-case-files/SKILL.md').read_bytes()
    mod.publish(lay, 'r-state', OLD, NEW, action='apply', candidate=new)
    record = (lay.records / 'r-state/record.json').read_bytes()
    lay.target.write_bytes(old)
    with pytest.raises(mod.PublicationError, match='state_conflict'):
        mod.publish(lay, 'r-state', OLD, NEW)
    assert (lay.records / 'r-state/record.json').read_bytes() == record


def test_cli_default_is_readonly_and_preproduction_identity_is_required(tmp_path, monkeypatch, capsys):
    import io
    mod, lay, old = layout(tmp_path)
    task = 'arn:aws:ecs:us-east-1:891612554546:task/supportportal-preproduction/' + 'a' * 32
    metadata = {'Family': 'supportportal-preproduction-hermes', 'TaskARN': task,
                'Cluster': 'arn:aws:ecs:us-east-1:891612554546:cluster/supportportal-preproduction'}
    def boundary(url, timeout):
        assert url == 'http://169.254.170.2/v4/synthetic/task' and timeout == 5
        return io.BytesIO(json.dumps(metadata).encode())
    monkeypatch.setenv('ECS_CONTAINER_METADATA_URI_V4', 'http://169.254.170.2/v4/synthetic')
    monkeypatch.setattr(mod.urllib.request, 'urlopen', boundary)
    monkeypatch.setattr(mod, 'PRODUCTION_LAYOUT', lay)
    monkeypatch.setattr(mod, 'BUNDLED_SKILL', ROOT / 'deployment/hermes-case-files/skills/support-case-files/SKILL.md')
    args = ['--release-id', 'r-cli', '--expected-old-sha256', OLD, '--expected-new-sha256', NEW, '--expected-task-arn', task]
    assert mod.main(args) == 0
    assert not lay.records.exists() and lay.target.read_bytes() == old
    assert mod.main(args + ['--action', 'apply']) == 0
    assert lay.target.read_bytes() == mod.BUNDLED_SKILL.read_bytes()
    assert mod.main(args + ['--action', 'rollback']) == 0
    assert lay.target.read_bytes() == old
    metadata['Family'] = 'supportportal-production-hermes'
    assert mod.main(args + ['--action', 'apply']) == 1
    assert lay.target.read_bytes() == old
    assert 'preproduction_task_identity_mismatch' in capsys.readouterr().out


def test_cli_rejects_arbitrary_metadata_url_and_unreviewed_hash(tmp_path, monkeypatch, capsys):
    mod, lay, old = layout(tmp_path)
    monkeypatch.setattr(mod, 'PRODUCTION_LAYOUT', lay)
    monkeypatch.setenv('ECS_CONTAINER_METADATA_URI_V4', 'http://localhost/metadata')
    args = ['--release-id', 'r-url', '--expected-old-sha256', OLD, '--expected-new-sha256', NEW, '--expected-task-arn', 'wrong']
    assert mod.main(args) == 1
    assert 'ecs_metadata_endpoint_required' in capsys.readouterr().out
    monkeypatch.setattr(mod, 'verify_preproduction_task', lambda value: None)
    args[3] = '0' * 64
    assert mod.main(args + ['--action', 'apply']) == 1
    assert 'unreviewed_payload_hashes' in capsys.readouterr().out
    assert lay.target.read_bytes() == old and not lay.records.exists()


def test_records_use_filesystem_identity_not_process_identity(tmp_path, monkeypatch):
    mod, lay, _ = layout(tmp_path)
    new = (ROOT / 'deployment/hermes-case-files/skills/support-case-files/SKILL.md').read_bytes()
    # An EFS AP may enforce 10000 while the process euid differs. A publisher
    # must not attempt to chown backups/records to the process identity.
    monkeypatch.setattr(mod.os, 'geteuid', lambda: 99999)
    monkeypatch.setattr(mod.os, 'getegid', lambda: 99999)
    result = mod.publish(lay, 'r-fs-identity', OLD, NEW, action='apply', candidate=new)
    assert result['state'] == 'committed'
    for name in ['record.json', 'SKILL.md.before']:
        info = (lay.records / 'r-fs-identity' / name).stat()
        assert (info.st_uid, info.st_gid, info.st_mode & 0o777) == (lay.uid, lay.gid, 0o600)


@pytest.mark.parametrize('state', ['prepared', 'outcome_unknown'])
@pytest.mark.parametrize('intent', ['apply', 'rollback'])
@pytest.mark.parametrize('target_new', [False, True])
def test_cli_check_only_rejects_unresolved_real_operations_readonly(tmp_path, monkeypatch, capsys,
                                                                 state, intent, target_new):
    import io
    mod, lay, old = layout(tmp_path)
    new = (ROOT / 'deployment/hermes-case-files/skills/support-case-files/SKILL.md').read_bytes()
    release = 'r-unresolved'
    if intent == 'rollback':
        mod.publish(lay, release, OLD, NEW, action='apply', candidate=new)
    replace = mod.os.replace
    # Process termination leaves prepared; an I/O fault records outcome_unknown.
    # Fail immediately before/after the actual target commit, preserving the
    # same on-disk operation used by production recovery.
    def interrupted_replace(src, dst):
        if Path(dst) == lay.target:
            if target_new == (intent == 'apply'):
                replace(src, dst)
            if state == 'prepared':
                raise SystemExit('publication process interrupted')
            raise OSError('publication result unknown')
        return replace(src, dst)
    monkeypatch.setattr(mod.os, 'replace', interrupted_replace)
    with pytest.raises(SystemExit if state == 'prepared' else mod.PublicationError):
        mod.publish(lay, release, OLD, NEW, action=intent, candidate=new)
    monkeypatch.setattr(mod.os, 'replace', replace)
    record_path = lay.records / release / 'record.json'
    record = json.loads(record_path.read_text())
    assert (record['state'], record['pending_action']) == (state, intent)
    assert lay.target.read_bytes() == (new if target_new else old)
    task = 'arn:aws:ecs:us-east-1:891612554546:task/supportportal-preproduction/' + 'b' * 32
    metadata = {'Family': 'supportportal-preproduction-hermes', 'TaskARN': task,
                'Cluster': 'supportportal-preproduction'}
    monkeypatch.setenv('ECS_CONTAINER_METADATA_URI_V4', 'http://169.254.170.2/v4/synthetic')
    monkeypatch.setattr(mod.urllib.request, 'urlopen', lambda *a, **k: io.BytesIO(json.dumps(metadata).encode()))
    monkeypatch.setattr(mod, 'PRODUCTION_LAYOUT', lay)
    def filesystem():
        return {str(p.relative_to(lay.data_root)): (p.read_bytes() if p.is_file() else None,
                p.stat().st_ino, p.stat().st_mtime_ns, p.stat().st_mode, p.stat().st_uid, p.stat().st_gid)
                for p in lay.data_root.rglob('*')}
    before = filesystem()
    args = ['--release-id', release, '--expected-old-sha256', OLD,
            '--expected-new-sha256', NEW, '--expected-task-arn', task]
    assert mod.main(args) == 1
    result = json.loads(capsys.readouterr().out.split('SKILL_PUBLICATION_JSON=')[1])
    assert result['ok'] is False and result['code'] == 'publication_unresolved'
    assert result['state'] == state and result['pending_action'] == intent
    assert result['current']['sha256'] == (NEW if target_new else OLD)
    assert filesystem() == before  # No lock/backup/record/target mutation by readback.
    # Explicit original operation, after readback, resolves the state. check-only
    # never does so implicitly or turns an unknown outcome into success.
    mod.publish(lay, release, OLD, NEW, action=intent, candidate=new)
    assert mod.publish(lay, release, OLD, NEW)['ok'] is True
    assert lay.target.read_bytes() == (new if intent == 'apply' else old)


@pytest.mark.parametrize('terminal', ['committed', 'rolled_back'])
def test_check_only_rejects_terminal_record_with_opposite_pending_intent(tmp_path, terminal):
    mod, lay, _ = layout(tmp_path)
    new = (ROOT / 'deployment/hermes-case-files/skills/support-case-files/SKILL.md').read_bytes()
    mod.publish(lay, 'r-intent', OLD, NEW, action='apply', candidate=new)
    if terminal == 'rolled_back':
        mod.publish(lay, 'r-intent', OLD, NEW, action='rollback')
    path = lay.records / 'r-intent/record.json'
    record = json.loads(path.read_text())
    record['pending_action'] = 'rollback' if terminal == 'committed' else 'apply'
    path.write_text(json.dumps(record))
    before = path.read_bytes(), lay.target.read_bytes()
    with pytest.raises(mod.PublicationError, match='state_conflict'):
        mod.publish(lay, 'r-intent', OLD, NEW)
    assert (path.read_bytes(), lay.target.read_bytes()) == before
