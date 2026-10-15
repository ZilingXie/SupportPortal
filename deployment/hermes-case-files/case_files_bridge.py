"""Hermes registry plugin extension. Caller cannot select case/turn/session."""
import base64
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

from supportportal_file_context import current_scope, publish_path


def _request(action, args):
    scope = current_scope()
    if scope is None or scope.get('phase') != 'work':
        raise ValueError('case_file_scope_required')
    base = os.environ['SUPPORTPORTAL_AGENT_API_BASE_URL'].rstrip('/')
    token = os.environ['SUPPORTPORTAL_AGENT_TOOL_TOKEN']
    request = urllib.request.Request(f'{base}/v1/agent/tools/case_files_{action}',
        data=json.dumps({**args, 'file_scope': scope['capability']}).encode(),
        headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'}, method='POST')
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        # Return only server-defined diagnostics; never echo a capability or URL.
        try:
            code = json.load(exc).get('detail', {}).get('code', 'case_file_api_refused')
        except Exception:
            code = 'case_file_api_refused'
        raise ValueError(code) from None


def _download(map_id):
    scope = current_scope()
    base = os.environ['SUPPORTPORTAL_AGENT_API_BASE_URL'].rstrip('/')
    request = urllib.request.Request(f'{base}/v1/agent/tools/case_files_download_stream',
        data=json.dumps({'map_id': map_id, 'file_scope': scope['capability']}).encode(),
        headers={'Authorization': 'Bearer ' + os.environ['SUPPORTPORTAL_AGENT_TOOL_TOKEN'],
                 'Content-Type': 'application/json'}, method='POST')
    target = Path(scope['directory']) / (str(int(map_id)) + '.part')
    try:
        with urllib.request.urlopen(request, timeout=120) as response, target.open('wb') as output:
            ref = json.loads(response.headers['X-SupportPortal-File'])
            size = int(ref['size_bytes'])
            if not 0 < size <= 50 * 1024 * 1024:
                raise ValueError('case_file_size_invalid')
            total = 0
            while True:
                chunk = response.read(512 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > size:
                    raise ValueError('case_file_download_size_mismatch')
                output.write(chunk)
            if total != size:
                raise ValueError('case_file_download_incomplete')
        name = ref['managed_filename']
        if Path(name).name != name:
            raise ValueError('case_file_filename_invalid')
        destination = target.with_name(name)
        target.replace(destination)
        return {'file': ref, 'path': str(destination)}
    finally:
        target.unlink(missing_ok=True)


def handler(action):
    def call(args, **kwargs):
        try:
            scope = current_scope()
            if not scope or scope.get('phase') != 'work':
                raise ValueError('case_file_scope_required')
            schemas = SCHEMAS[action]
            if set(args) - set(schemas['properties']):
                raise ValueError('case_file_parameters_invalid')
            body = dict(args)
            if action == 'publish':
                path = publish_path(body.pop('path'))
                body['filename'] = path.name
                body['data_base64'] = base64.b64encode(path.read_bytes()).decode()
            if action == 'download':
                return json.dumps(_download(body['map_id']), ensure_ascii=False)
            return json.dumps(_request(action, body), ensure_ascii=False)
        except Exception as exc:
            code = str(exc) if isinstance(exc, ValueError) else 'case_file_transport_failed'
            return json.dumps({'ok': False, 'code': code})
    return call


def _schema(properties, required=()):
    return {'type': 'object', 'properties': properties, 'required': list(required), 'additionalProperties': False}


STRING = {'type': 'string'}
INTEGER = {'type': 'integer'}
SCHEMAS = {
    'list': _schema({k: STRING for k in ('source', 'extension', 'purpose', 'introduced_turn_id')}),
    'download': _schema({'map_id': INTEGER}, ('map_id',)),
    'read': _schema({'map_id': INTEGER, 'start_line': INTEGER, 'line_count': INTEGER, 'member': STRING}, ('map_id',)),
    'search': _schema({'map_id': INTEGER, 'query': STRING, 'member': STRING}, ('map_id', 'query')),
    'publish': _schema({'path': STRING, 'operation_id': STRING, 'content_type': STRING}, ('path', 'operation_id')),
}


def register(ctx):
    for action, schema in SCHEMAS.items():
        name = 'support_case_files_' + action
        ctx.register_tool(name=name, toolset='supportportal_files',
            schema={'name': name, 'description': f'{action} files in the current bound Investigation case; read support-case-files.', 'parameters': schema},
            handler=handler(action), check_fn=lambda: current_scope() is not None and current_scope().get('phase') == 'work',
            requires_env=['SUPPORTPORTAL_AGENT_API_BASE_URL', 'SUPPORTPORTAL_AGENT_TOOL_TOKEN'], emoji='📎')
