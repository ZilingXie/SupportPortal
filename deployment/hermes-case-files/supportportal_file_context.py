"""Private, run-local file authority. Never expose capability to model/tool schemas."""
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from tempfile import TemporaryDirectory

_SCOPE = ContextVar('supportportal_case_file_scope', default=None)


def current_scope():
    return _SCOPE.get()


def skill_allowed(name):
    if 'support-case-files' not in str(name).replace('\\', '/').replace(':', '/').split('/'):
        return True
    scope = current_scope()
    return scope is not None and scope.get("phase") == "work"


@contextmanager
def bind_file_scope(scope, session_id, workspace):
    valid = isinstance(scope, dict) and scope.get('session_id') == session_id and all(
        scope.get(k) for k in ('turn_id', 'case_id', 'request_id', 'namespace', 'capability', 'phase'))
    with TemporaryDirectory(prefix='hermes-case-files-') as directory:
        value = {**scope, 'directory': directory, 'workspace': str(workspace or '')} if valid else None
        token = _SCOPE.set(value)
        try:
            yield
        finally:
            _SCOPE.reset(token)


def publish_path(path):
    scope = current_scope()
    if not scope or not scope['workspace']:
        raise ValueError('case_file_workspace_unavailable')
    root = Path(scope['workspace']).resolve()
    candidate = Path(path)
    candidate = (root / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()
    if not candidate.is_relative_to(root) and not candidate.is_relative_to(Path(scope['directory']).resolve()):
        raise ValueError('case_file_path_outside_run')
    if not candidate.is_file() or not 0 < candidate.stat().st_size <= 50 * 1024 * 1024:
        raise ValueError('case_file_size_invalid')
    return candidate
