"""Bound Investigation file operations. Sources are verified at ingress, not by LLM labels."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import tempfile
import zipfile
import io
from pathlib import Path

from backend.services import investigation_attachments as transfer
from backend.services.automation_hermes_tools import HermesToolError

TEXT_EXTENSIONS = {'.txt', '.log', '.json', '.csv', '.tsv', '.md', '.html', '.htm', '.xml', '.yaml', '.yml'}
MAX_TEXT_BYTES = 2 * 1024 * 1024
CHUNK_BYTES = 512 * 1024


def enabled():
    return os.getenv('INVESTIGATION_CASE_FILES_ENABLED', '0') == '1'


def run_scope(store, turn, binding, request_id, phase):
    """Private request metadata; never included in instructions/model arguments."""
    if not enabled() or phase not in {'work', 'persona'} or turn.get('direction') != 'investigation' or binding.get('session_kind', 'case') != 'case':
        return None
    if not binding.get('slack_channel_id') or not binding.get('slack_thread_ts'):
        return None
    secret = str(os.getenv('HERMES_AGENT_API_TOKEN') or '').strip()
    if not secret:
        raise HermesToolError('file_scope_secret_missing', 'file scope signing is not configured')
    payload = {'namespace': store.settings.job_namespace, 'case_id': turn['zendesk_ticket_id'],
               'turn_id': turn['turn_id'], 'session_id': binding['hermes_session_id'], 'request_id': request_id, 'phase': phase}
    raw = base64.urlsafe_b64encode(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()).decode()
    signature = hmac.new(secret.encode(), ('case-files-v1:' + raw).encode(), hashlib.sha256).hexdigest()
    return {'capability': raw + '.' + signature, **payload}


def context(store, scope, *, expected_phase='work'):
    if not enabled():
        raise HermesToolError('case_files_disabled', 'case file tools are disabled')
    try:
        raw, signature = str(scope).split('.')
        secret = str(os.getenv('HERMES_AGENT_API_TOKEN') or '').strip()
        expected = hmac.new(secret.encode(), ('case-files-v1:' + raw).encode(), hashlib.sha256).hexdigest()
        if not secret or not hmac.compare_digest(signature, expected):
            raise ValueError()
        identity = json.loads(base64.urlsafe_b64decode(raw))
        turn = store.get_hermes_turn(identity['turn_id'])
        binding = store.get_hermes_case_binding(identity['case_id'])
        if not turn or not binding or identity['namespace'] != store.settings.job_namespace:
            raise ValueError()
        if turn.get('namespace') != identity['namespace'] or turn['zendesk_ticket_id'] != identity['case_id']:
            raise ValueError()
        if turn['status'] != 'running' or turn.get('phase') != expected_phase or identity.get('phase') != expected_phase or turn.get('direction') != 'investigation':
            raise ValueError()
        if binding.get('direction') != 'investigation' or binding.get('session_kind', 'case') != 'case' or binding['hermes_session_id'] != identity['session_id']:
            raise ValueError()
        run = store.get_or_create_hermes_turn_run(identity['turn_id'], expected_phase)
        if run['request_id'] != identity['request_id'] or run['status'] not in {'pending', 'running'}:
            raise ValueError()
        if not binding.get('slack_channel_id') or not binding.get('slack_thread_ts'):
            raise ValueError()
        from backend.services.automation_ecs_store import native_customer_lifecycle_blocker
        case = store.get_case_mirror(identity['case_id']) or {}
        if native_customer_lifecycle_blocker(binding, case) or int(case.get('case_revision') or 0) != int(turn['case_revision']):
            raise ValueError()
    except (ValueError, KeyError, TypeError):
        raise HermesToolError('file_scope_invalid', 'active bound Investigation run scope required') from None
    return turn, binding


def reference(row):
    p = row['provenance']
    return {'map_id': row['id'], 'map_version': row['version'], 'case_id': row['case_id'],
        'slack_file_id': row['slack_file_id'], 'file_name': row['managed_filename'],
        'managed_filename': row['managed_filename'], 'original_filename': row['original_filename'],
        'slack_filename': row['slack_filename'], 'content_type': row['content_type'], 'size_bytes': row['size_bytes'],
        'channel_id': p['channel_id'], 'thread_ts': p['thread_ts'], 'source_message_ts': p.get('message_ts'),
        'source_event_id': p['source_event_id'], 'actor_id': p.get('uploader_id'),
        'source_system': row['source_system'], 'source_session_id': p.get('session_id'),
        'introduced_turn_id': p.get('turn_id'), 'state': row['state'], 'purpose': row['purpose']}


def register_engineer(store, case_id, refs, team_id):
    results = []
    for ref in refs:  # Already verified against Slack by the authenticated ingress.
        row = store.register_case_file(case_id=case_id, source_system='engineer', source_file_id=ref['slack_file_id'],
            original_filename=ref['file_name'], content_type=ref['content_type'], size_bytes=ref['size_bytes'], purpose='supplement',
            provenance={'team_id': team_id, 'channel_id': ref['channel_id'], 'thread_ts': ref['thread_ts'],
                'message_ts': ref['source_message_ts'], 'source_event_id': ref['source_event_id'],
                'provider_id': ref['actor_id'], 'uploader_id': ref['actor_id']})
        # Preserve the first verified provenance on duplicate file events.
        row = store.update_case_file(case_id, row['id'], slack_file_id=ref['slack_file_id'],
                                    slack_filename=row['original_filename'], state='available', failure_code=None)
        ref = reference(row)
        ref.pop("introduced_turn_id", None)  # Assigned after ingress; keep retried event payload byte-stable.
        results.append(ref)
    return results


def import_legacy_engineer_files(store, case_id):
    """On-demand metadata discovery from server-owned historical turns; no re-upload."""
    for turn in store.list_hermes_case_turns(case_id, limit=100):
        for ref in (turn.get('work_result') or {}).get('attachments') or []:
            if ref.get('map_id') or not all(ref.get(k) for k in ('slack_file_id', 'actor_id', 'source_event_id', 'source_message_ts')):
                continue
            if any(r['source_system'] == 'engineer' and r['source_file_id'] == ref['slack_file_id'] for r in store.list_case_files(case_id)):
                continue
            row = store.register_case_file(case_id=case_id, source_system='engineer', source_file_id=ref['slack_file_id'],
                original_filename=ref['file_name'], content_type=ref['content_type'], size_bytes=ref['size_bytes'], purpose='supplement',
                provenance={'channel_id': ref['channel_id'], 'thread_ts': ref['thread_ts'], 'message_ts': ref['source_message_ts'],
                    'source_event_id': ref['source_event_id'], 'provider_id': ref['actor_id'], 'uploader_id': ref['actor_id'], 'turn_id': turn['turn_id']})
            try:
                validated = transfer.validate_slack_files([{'file_id': ref['slack_file_id']}], channel_id=ref['channel_id'],
                    thread_ts=ref['thread_ts'], message_ts=ref['source_message_ts'], actor=ref['actor_id'], source_event_id=ref['source_event_id'])[0]
                if any(validated[k] != ref[k] for k in ('file_name', 'size_bytes', 'content_type')):
                    raise ValueError('attachment_source_changed')
                store.update_case_file(case_id, row['id'], slack_file_id=ref['slack_file_id'], slack_filename=ref['file_name'], state='available')
            except (ValueError, OSError, transfer.EngineerSlackDeliveryError) as exc:
                store.update_case_file(case_id, row['id'], state='unavailable', failure_code=getattr(exc, 'code', type(exc).__name__))


def confirm_slack(store, row, file_id, *, expected_filename=None):
    file = transfer._slack('files.info', {'file': file_id}).get('file') or {}
    p = row['provenance']
    shares = (file.get('shares') or {}).get('private', {}) | (file.get('shares') or {}).get('public', {})
    share = next((s for s in shares.get(p['channel_id'], []) if s.get('thread_ts') == p['thread_ts']), None)
    if file.get('id') != file_id or not share or not share.get('ts') or not file.get('user') or file.get('is_external') or file.get('mode') == 'tombstone':
        raise ValueError('case_file_share_unconfirmed')
    expected_filename = expected_filename or row.get('slack_filename') or row['managed_filename']
    if file.get('name') != expected_filename or int(file.get('size') or 0) != row['size_bytes']:
        raise ValueError('case_file_source_changed')
    p = {**p, 'message_ts': share['ts'], 'uploader_id': file['user'],
         'team_id': file.get('source_team') or file.get('user_team') or os.getenv('ENGINEER_SLACK_TEAM_ID'),
         'source_content_type': p.get('source_content_type', row['content_type'])}
    return store.update_case_file(row['case_id'], row['id'], slack_file_id=file_id,
        slack_filename=file['name'], content_type=file.get('mimetype') or row['content_type'], provenance=p, state='available', failure_code=None)


def download(store, row, *, output=None):
    if row['state'] != 'available':
        raise ValueError('case_file_not_available')
    binding = store.get_hermes_case_binding(row['case_id']) or {}
    p = row['provenance']
    if binding.get('slack_channel_id') != p['channel_id'] or binding.get('slack_thread_ts') != p['thread_ts']:
        raise ValueError('case_file_binding_changed')
    try:
        return transfer.download_slack_attachment(reference(row), **({"output": output} if output is not None else {}))
    except (ValueError, OSError, transfer.EngineerSlackDeliveryError) as exc:
        store.update_case_file(row['case_id'], row['id'], state='unavailable', failure_code=getattr(exc, 'code', type(exc).__name__))
        raise


def selected_references(store, case_id, ids):
    if not isinstance(ids, list) or len(ids) > 10 or any(type(fid) is not int or fid <= 0 for fid in ids) or len(set(ids)) != len(ids):
        raise HermesToolError('case_file_selection_invalid', 'select up to ten distinct Map IDs')
    results = []
    for fid in ids:
        row = store.get_case_file(case_id, fid)
        if not row or row['state'] != 'available':
            raise HermesToolError('case_file_not_available', 'selected file must belong to this case and be available')
        results.append(reference(row))
    return results


def _text(data, filename):
    if Path(filename).suffix.lower() not in TEXT_EXTENSIONS:
        raise ValueError('case_file_content_unsupported_download_only')
    if len(data) > MAX_TEXT_BYTES:
        raise ValueError('case_file_text_limit_use_download')
    try:
        text = data.decode('utf-8-sig')
    except UnicodeDecodeError:
        raise ValueError('case_file_encoding_unsupported_download_only') from None
    if '\x00' in text:
        raise ValueError('case_file_binary_unsupported_download_only')
    return text


def _read_text_stream(stream, filename, action, args, ref):
    if Path(filename).suffix.lower() not in TEXT_EXTENSIONS:
        raise ValueError('case_file_content_unsupported_download_only')
    start, count = int(args.get('start_line', 1)), int(args.get('line_count', 100))
    query = str(args.get('query', ''))
    if action == 'search' and (not query or len(query) > 512):
        raise ValueError('case_file_search_invalid')
    if start < 1 or not 1 <= count <= 200:
        raise ValueError('case_file_range_invalid')
    results = []
    # Scan large logs on disk. Keep at most one bounded line and the selected output.
    text = io.TextIOWrapper(stream, encoding='utf-8-sig', errors='strict')
    try:
        number = 0
        while True:
            line = text.readline(MAX_TEXT_BYTES + 1)
            if not line:
                break
            number += 1
            if len(line) > MAX_TEXT_BYTES or '\x00' in line:
                raise ValueError('case_file_binary_or_line_limit_use_download')
            line = line.rstrip('\r\n')
            selected = query in line if action == 'search' else start <= number < start + count
            if selected:
                results.append({'line': number, 'text': line[:2000]})
            if action == 'search' and len(results) > 100:
                break
            if action == 'read' and number >= start + count - 1:
                break
    except UnicodeDecodeError:
        raise ValueError('case_file_encoding_unsupported_download_only') from None
    finally:
        text.detach()
    common = {'file': ref, 'member': args.get('member')}
    if action == 'search':
        return {**common, 'matches': results[:100], 'truncated': len(results) > 100}
    return {**common, 'start_line': start, 'lines': results}


def download_for_bridge(store, *, scope, map_id):
    turn, _ = context(store, scope)
    if type(map_id) is not int or map_id <= 0:
        raise HermesToolError('case_file_parameters_invalid', 'positive Map ID required')
    row = store.get_case_file(turn['zendesk_ticket_id'], map_id)
    if not row:
        raise HermesToolError('case_file_not_found', 'file does not belong to this case')
    temporary = tempfile.NamedTemporaryFile(prefix='support-case-file-', delete=False)
    try:
        download(store, row, output=temporary)
        return temporary.name, reference(row)
    except Exception as exc:
        os.unlink(temporary.name)
        code = str(exc) if isinstance(exc, ValueError) else getattr(exc, 'code', 'case_file_download_failed')
        raise HermesToolError(code, code) from None
    finally:
        temporary.close()


def tool_case_files(store, repository, *, scope, action, args):
    turn, binding = context(store, scope)
    # No model-supplied case, turn, session, URL or source provenance is accepted.
    allowed = {'list': {'source', 'extension', 'purpose', 'introduced_turn_id'},
        'download': {'map_id', 'offset'}, 'read': {'map_id', 'start_line', 'line_count', 'member'},
        'search': {'map_id', 'query', 'member'}, 'publish': {'operation_id', 'filename', 'content_type', 'data_base64'}}
    if action not in allowed or set(args) - allowed[action]:
        raise HermesToolError('case_file_parameters_invalid', 'unsupported file parameters')
    case_id = turn['zendesk_ticket_id']
    tmp = None
    try:
        if action == 'publish':
            if len(str(args.get('data_base64', ''))) > (transfer.MAX_FILE_BYTES + 2) // 3 * 4:
                raise ValueError('attachment_file_size_invalid')
            data = base64.b64decode(args.get('data_base64', ''), validate=True)
            return publish(store, repository, turn=turn, binding=binding, data=data,
                filename=args.get('filename', ''), content_type=args.get('content_type', 'application/octet-stream'),
                operation_id=args.get('operation_id', ''))
        if action == 'list':
            import_legacy_engineer_files(store, case_id)
            rows = store.list_case_files(case_id)
            for key, column in [('source', 'source_system'), ('purpose', 'purpose')]:
                if args.get(key):
                    rows = [r for r in rows if r[column] == args[key]]
            if args.get('extension'):
                rows = [r for r in rows if Path(r['managed_filename']).suffix.lower() == args['extension'].lower()]
            if args.get('introduced_turn_id'):
                rows = [r for r in rows if r['provenance'].get('turn_id') == args['introduced_turn_id']]
            return {'files': [reference(r) for r in rows]}
        row = store.get_case_file(case_id, int(args['map_id']))
        if not row:
            raise ValueError('case_file_not_found')
        tmp = tempfile.TemporaryFile()
        download(store, row, output=tmp)
        ref = reference(row)
        if action == 'download':
            offset = int(args.get('offset', 0))
            if offset < 0 or offset > row['size_bytes']:
                raise ValueError('case_file_offset_invalid')
            tmp.seek(offset)
            chunk = tmp.read(CHUNK_BYTES)
            return {'file': ref, 'offset': offset, 'data_base64': base64.b64encode(chunk).decode(),
                    'next_offset': offset + len(chunk), 'eof': offset + len(chunk) == row['size_bytes']}
        name = row['managed_filename']
        if Path(name).suffix.lower() != '.zip':
            return _read_text_stream(tmp, name, action, args, ref)
        if Path(name).suffix.lower() == '.zip':
            with zipfile.ZipFile(tmp) as archive:
                members = archive.infolist()
                if len(members) > 2000:
                    raise ValueError('case_file_zip_directory_limit')
                if not args.get('member'):
                    return {'file': ref, 'members': [{'name': m.filename, 'size': m.file_size} for m in members]}
                member = args['member']
                info = archive.getinfo(member)
                if info.file_size > MAX_TEXT_BYTES or info.flag_bits & 1 or sum(m.filename == member for m in members) != 1:
                    raise ValueError('case_file_zip_member_unsupported')
                name = member
                with archive.open(info) as stream:
                    data = stream.read(MAX_TEXT_BYTES + 1)
        else:
            data = tmp.read(MAX_TEXT_BYTES + 1)
        text = _text(data, name)
        lines = text.splitlines()
        if action == 'search':
            query = str(args.get('query', ''))
            if not query or len(query) > 512:
                raise ValueError('case_file_search_invalid')
            matches = [{'line': i + 1, 'text': line[:2000]} for i, line in enumerate(lines) if query in line]
            return {'file': ref, 'member': args.get('member'), 'matches': matches[:100], 'truncated': len(matches) > 100}
        start, count = int(args.get('start_line', 1)), int(args.get('line_count', 100))
        if start < 1 or not 1 <= count <= 200:
            raise ValueError('case_file_range_invalid')
        return {'file': ref, 'member': args.get('member'), 'start_line': start,
                'lines': [{'line': i + start, 'text': line[:2000]} for i, line in enumerate(lines[start - 1:start - 1 + count])]}
    except (ValueError, TypeError, KeyError, OSError, zipfile.BadZipFile, transfer.EngineerSlackDeliveryError) as exc:
        code = str(exc) if isinstance(exc, ValueError) else getattr(exc, 'code', 'case_file_operation_failed')
        raise HermesToolError(code, code) from None
    finally:
        if tmp is not None:
            tmp.close()


def publish(store, repository, *, turn, binding, data, filename, content_type, operation_id, source='hermes', session_id=None, source_message_id=None):
    """Trusted source adapter; Zac calls this only after authenticated acquisition."""
    from backend.services.automation_native_notifications import now
    from backend.services.engineer_slack import engineer_slack_outbound_disabled
    if repository is None or not operation_id or len(operation_id) > 256 or not filename or Path(filename).name != filename:
        raise ValueError('case_file_publish_identity_invalid')
    if not data or len(data) > transfer.MAX_FILE_BYTES:
        raise ValueError('attachment_file_size_invalid')
    if source not in {'hermes', 'zac'}:
        raise ValueError('case_file_source_invalid')
    if source == 'zac' and (not session_id or not source_message_id):
        raise ValueError('zac_file_provenance_required')
    case_id = turn['zendesk_ticket_id']
    row = store.register_case_file(case_id=case_id, source_system=source,
        source_file_id=f'{session_id or turn["turn_id"]}:{operation_id}', original_filename=filename,
        content_type=content_type, size_bytes=len(data), purpose='report',
        provenance={'channel_id': binding['slack_channel_id'], 'thread_ts': binding['slack_thread_ts'],
            'source_event_id': operation_id, 'turn_id': turn['turn_id'], 'session_id': session_id or binding['hermes_session_id'],
            'source_message_id': source_message_id, 'provider_id': source})
    if row['size_bytes'] != len(data) or row['original_filename'] != filename or row['provenance'].get('source_content_type', row['content_type']) != content_type:
        raise ValueError('case_file_operation_identity_conflict')
    scope = f'native-hermes-notification:{store.settings.job_namespace}'
    key = f'attachment:case-file:{row["id"]}'
    ledger = repository.enqueue_native_notification(scope=scope, key=key, created_at=now(),
        payload={'kind': 'generated_attachment', 'map_id': row['id'], 'ticket_id': case_id})
    if row['state'] == 'available' and ledger['state'] == 'completed':
        return {'file': reference(row), 'replayed': True}
    if ledger['state'] in {'sending', 'outcome_unknown', 'completed'}:
        file_id = ledger['response_payload'].get('slack_file_id')
        if file_id:
            try:
                row = confirm_slack(store, row, file_id)
                if ledger['state'] != 'completed' and not repository.confirm_native_attachment_readback(scope=scope, key=key, file_id=file_id,
                        result={'status': 'confirmed_readback', 'file_id': file_id}, updated_at=now()):
                    store.update_case_file(case_id, row['id'], state='pending', failure_code='attachment_receipt_unpersisted')
                    raise ValueError('attachment_receipt_unpersisted')
                if source == 'zac':
                    store.bind_case_zac_session(case_id, session_id)
                return {'file': reference(row), 'replayed': True}
            except (ValueError, OSError, transfer.EngineerSlackDeliveryError):
                pass
        return {'status': 'outcome_unknown', 'map_id': row['id'], 'file_id': file_id}
    if engineer_slack_outbound_disabled():
        raise ValueError('native_slack_outbound_disabled')
    claim = turn['turn_id']
    if not repository.claim_native_notification(scope=scope, key=key, claim_token=claim, updated_at=now()):
        return {'status': 'not_claimed', 'map_id': row['id']}
    def persist(fid):
        if not repository.checkpoint_native_notification(scope=scope, key=key, claim_token=claim,
                fields={'slack_file_id': fid}, updated_at=now()):
            raise ValueError('attachment_receipt_checkpoint_failed')
        store.update_case_file(case_id, row['id'], slack_file_id=fid)
    try:
        receipt = transfer.upload_slack_attachment(data=data, file_name=row['managed_filename'],
            channel_id=binding['slack_channel_id'], thread_ts=binding['slack_thread_ts'], persist_file_id=persist)
        row = confirm_slack(store, row, receipt['file_id'])
        if not repository.finish_native_notification(scope=scope, key=key, claim_token=claim, state='completed', result=receipt, updated_at=now()):
            raise ValueError('attachment_receipt_unpersisted')
        if source == 'zac':
            store.bind_case_zac_session(case_id, session_id)
        return {'file': reference(row)}
    except Exception as exc:
        # After slot reservation even a failed readback cannot justify a re-upload.
        reserved = store.get_case_file(case_id, row['id'])['slack_file_id']
        state = 'outcome_unknown' if reserved else 'failed'
        repository.finish_native_notification(scope=scope, key=key, claim_token=claim, state=state,
            result={'status': state, 'failure_code': getattr(exc, 'code', type(exc).__name__)}, updated_at=now())
        store.update_case_file(case_id, row['id'], state='pending' if reserved else 'unavailable',
            failure_code=getattr(exc, 'code', type(exc).__name__))
        return {'status': state, 'map_id': row['id']}


def import_zac_file(store, repository, *, case_id, session_id, message_id, source_file_id, acquire):
    """Integration seam for an authenticated Zac acquisition adapter, not an LLM tool.

    acquire(session_id, message_id, file_id) must resolve/authorize the source
    and return {data: bytes, filename: str, content_type: str}. No caller URL.
    Live adapter remains waiting-for-evidence until its actual protocol is known.
    """
    from backend.services.automation_ecs_store import native_customer_lifecycle_blocker
    binding = store.get_hermes_case_binding(case_id) or {}
    case = store.get_case_mirror(case_id) or {}
    if binding.get('direction') != 'investigation' or binding.get('session_kind', 'case') != 'case' or native_customer_lifecycle_blocker(binding, case):
        raise ValueError('zac_case_binding_invalid')
    turns = store.list_hermes_case_turns(case_id, limit=1)
    if not turns or not session_id or not message_id or not source_file_id:
        raise ValueError('zac_file_provenance_required')
    result = acquire(session_id, message_id, source_file_id)
    if not isinstance(result, dict) or not isinstance(result.get('data'), bytes):
        raise ValueError('zac_file_acquisition_invalid')
    store.bind_case_zac_session(case_id, session_id)
    return publish(store, repository, turn=turns[0], binding=binding, source='zac', session_id=session_id,
        source_message_id=message_id, operation_id=source_file_id, data=result['data'], filename=result['filename'],
        content_type=result.get('content_type', 'application/octet-stream'))
