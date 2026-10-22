"""R2 contracts through real Slack ingress, tool API, PG and registry bridge."""
import ast
import base64
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import importlib.util
import io
import json
from pathlib import Path
import shutil
import sys
import zipfile
from unittest.mock import patch, Mock

import pytest
from fastapi.testclient import TestClient

from backend.automation_ecs_api import create_app
from backend.services.automation_ecs_dashboard_auth import DashboardAuthConfig
from backend.services.automation_hermes_agent import toolsets_for_phase
from backend.services.investigation_case_files import reference, run_scope, publish, import_zac_file
from backend.services.investigation_case_file_store import normalize_short_description
from backend.tests.test_investigation_route_status_fix import route_store, pg_store, finish_initial
from backend.tests.test_investigation_attachments import FILE, MESSAGE, send
from backend.tests.test_native_hermes_notifications import repository, TIME

BUNDLE = Path(__file__).resolve().parents[2] / 'deployment/hermes-case-files'
DATA = b'first\nneedle: verified log sample\nlast\n'


def test_source_aware_default_descriptions_and_control_rejection():
    assert normalize_short_description('', managed_filename='1-zendesk-2.zip', source_system='zendesk') == 'Customer attachment 1-zendesk-2.zip'
    assert normalize_short_description('', managed_filename='1-hermes-3.md', source_system='hermes') == 'Hermes report 1-hermes-3.md'
    assert normalize_short_description('', managed_filename='1-zac-4.log', source_system='zac') == 'Zac file 1-zac-4.log'
    with pytest.raises(ValueError, match='case_file_description_invalid'):
        normalize_short_description('bad\x00text')


@pytest.fixture
def bound(route_store, monkeypatch):
    monkeypatch.setenv('HERMES_AGENT_API_TOKEN', 'synthetic-case-file-signing-key')
    monkeypatch.setenv('ENGINEER_SLACK_BOT_ID', 'B08RHT5TZV2')
    monkeypatch.setenv('INVESTIGATION_CASE_FILES_ENABLED', '0')
    finish_initial(route_store)
    route_store.bind_hermes_case_thread('123', channel_id='C-TEST', thread_ts='123.45')
    monkeypatch.setenv('INVESTIGATION_CASE_FILES_ENABLED', '1')
    file = {**FILE, 'name': 'rtc.log', 'mimetype': 'text/plain', 'size': len(DATA)}
    with patch('backend.services.investigation_attachments._slack', return_value={'file': file}), patch('backend.services.investigation_attachments._download', side_effect=transport()):
        result = send(route_store)
        assert result['ok'], result
        assert send(route_store)['turn_id'] == result['turn_id']
    tid = result['turn_id']
    route_store.start_hermes_agent_turn(tid, run_id=None)
    run = route_store.get_or_create_hermes_turn_run(tid, 'work')
    turn = route_store.get_hermes_turn(tid)
    binding = route_store.get_hermes_case_binding('123')
    scope = run_scope(route_store, turn, binding, run['request_id'], 'work')
    auth = DashboardAuthConfig(session_secret='case-files-test-session-secret-long-enough', session_ttl_seconds=120)
    app = create_app(settings=replace(route_store.settings, service_role='api'), store=route_store, dashboard_auth=auth)
    client = TestClient(app)
    return route_store, client, scope, file


def api(bound, action, **args):
    store, client, scope, _ = bound
    with patch('backend.automation_ecs_api._engineer_ticket_repository', return_value=None):
        return client.post('/automation/preproduction/v1/agent/tools/case_files_' + action,
            headers={'Authorization': 'Bearer ' + store.settings.intake_shared_token},
            json={'file_scope': scope['capability'], **args})


def transport(data=DATA):
    def receive(url, auth, *, provider, output=None):
        if output is not None:
            output.write(data); output.seek(0)
            return b''
        return data
    return receive


def test_engineer_names_dedupe_api_reads_search_and_download(bound):
    store, _, _, file = bound
    row = store.list_case_files('123')[0]
    assert row['managed_filename'] == f'123-engineer-{row["id"]}.log'
    assert row['original_filename'] == row['slack_filename'] == 'rtc.log'
    assert len(store.list_case_files('123')) == 1
    # Same filename is not identity. A distinct Slack file gets a distinct Map ID/name.
    with patch('backend.services.investigation_attachments._slack', return_value={'file': {**file, 'id': 'F456'}}), patch('backend.services.investigation_attachments._download', side_effect=transport()):
        assert send(store, {**MESSAGE, 'source_event_id': 'Ev-other', 'files': [{'file_id': 'F456'}]})['ok']
    rows = store.list_case_files('123')
    assert len(rows) == 2 and rows[0]['managed_filename'] != rows[1]['managed_filename']
    assert all(r['slack_filename'] == 'rtc.log' for r in rows)
    with patch('backend.services.investigation_attachments._slack', return_value={'file': file}), patch('backend.services.investigation_attachments._download', side_effect=transport()):
        result = api(bound, 'read', map_id=row['id'], start_line=2, line_count=1)
        assert result.status_code == 200, result.text
        assert result.json()['lines'] == [{'line': 2, 'text': 'needle: verified log sample'}]
        assert result.json()['file']['map_id'] == row['id']
        assert api(bound, 'search', map_id=row['id'], query='needle').json()['matches'][0]['line'] == 2
        result = api(bound, 'download', map_id=row['id'])
        assert base64.b64decode(result.json()['data_base64']) == DATA
    assert api(bound, 'list', source='engineer', extension='.log').status_code == 200


def test_case_scope_forged_parameters_and_disabled_phase_reject_before_download(bound, monkeypatch):
    store, _, scope, _ = bound
    row = store.register_case_file(case_id='456', source_system='engineer', source_file_id='FOREIGN',
        original_filename='rtc.log', content_type='text/plain', size_bytes=3, purpose='supplement', provenance={})
    with patch('backend.services.investigation_attachments._download') as download:
        for args, code in [({'map_id': row['id']}, 'case_file_not_found'),
                           ({'map_id': 1, 'turn_id': 'foreign'}, 'case_file_parameters_invalid'),
                           ({'map_id': 1, 'case_id': '456'}, 'case_file_parameters_invalid'),
                           ({'map_id': 1, 'file_scope': scope['capability'] + 'x'}, 'file_scope_invalid')]:
            result = api(bound, 'read', **args)
            assert result.status_code == 422, result.text
            assert result.json()['detail']['code'] == code
        monkeypatch.setenv('INVESTIGATION_CASE_FILES_ENABLED', '0')
        assert api(bound, 'list').json()['detail']['code'] == 'case_files_disabled'
        download.assert_not_called()
    for phase, direction, kind in [('route', 'investigation', 'case'), ('work', 'automation', 'case'), ('work', 'investigation', 'adhoc')]:
        assert 'supportportal_files' not in (toolsets_for_phase(phase, direction=direction, session_kind=kind) or [])


def test_deleted_permission_size_failure_marks_unavailable(bound):
    store, _, _, file = bound
    fid = store.list_case_files('123')[0]['id']
    from backend.services.engineer_slack import EngineerSlackDeliveryError
    with patch('backend.services.investigation_attachments._slack', side_effect=EngineerSlackDeliveryError('attachment_slack_file_not_found')), patch('backend.services.investigation_attachments._download') as download:
        result = api(bound, 'read', map_id=fid)
        assert result.status_code == 422
        download.assert_not_called()
    assert store.get_case_file('123', fid)['state'] == 'unavailable'
    assert store.get_case_file('123', fid)['failure_code'] == 'attachment_slack_file_not_found'
    # Malicious size rejected by ingress, not hidden by the API harness.
    with patch('backend.services.investigation_attachments._slack', return_value={'file': {**file, 'size': 50 * 1024 * 1024 + 1}}):
        assert send(store, {**MESSAGE, 'source_event_id': 'too-large'})['detail'] == 'attachment_file_size_invalid'


def test_zip_directory_member_and_unsupported_content(bound):
    store, _, _, file = bound
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as z:
        z.writestr('rtc.log', DATA)
        z.writestr('image.bin', b'\x00')
    data = buffer.getvalue()
    row = store.register_case_file(case_id='123', source_system='engineer', source_file_id='ZIP', original_filename='logs.zip',
        content_type='application/zip', size_bytes=len(data), purpose='supplement', provenance=store.list_case_files('123')[0]['provenance'])
    store.update_case_file('123', row['id'], state='available', slack_file_id='ZIP', slack_filename='logs.zip')
    file = {**file, 'id': 'ZIP', 'name': 'logs.zip', 'size': len(data), 'mimetype': 'application/zip'}
    with patch('backend.services.investigation_attachments._slack', return_value={'file': file}), patch('backend.services.investigation_attachments._download', side_effect=transport(data)):
        assert api(bound, 'read', map_id=row['id']).json()['members'][0]['name'] == 'rtc.log'
        assert api(bound, 'search', map_id=row['id'], member='rtc.log', query='needle').json()['matches'][0]['line'] == 2
        assert api(bound, 'read', map_id=row['id'], member='image.bin').json()['detail']['code'] == 'case_file_content_unsupported_download_only'


def test_postgres_parallel_registration_returns_one_map_and_name(bound):
    store = bound[0]
    args = dict(case_id='123', source_system='engineer', source_file_id='CONCURRENT', original_filename='rtc.log',
        content_type='text/plain', size_bytes=3, purpose='supplement', provenance={'source_event_id': 'concurrent'})
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(lambda _: store.register_case_file(**args), range(8)))
    assert len({r['id'] for r in rows}) == len({r['managed_filename'] for r in rows}) == 1
    assert len([r for r in store.list_case_files('123') if r['source_file_id'] == 'CONCURRENT']) == 1


def test_generated_unknown_readback_and_zac_session(bound, repository, monkeypatch):
    store, _, scope, _ = bound
    turn, binding = store.get_hermes_turn(scope['turn_id']), store.get_hermes_case_binding('123')
    from backend.services.engineer_slack import EngineerSlackDeliveryError
    def uncertain(**kwargs):
        kwargs['persist_file_id']('GENERATED')
        raise EngineerSlackDeliveryError('timeout', outcome_unknown=True)
    monkeypatch.setenv('ENGINEER_SLACK_OUTBOUND_DISABLED', '0')
    args = dict(turn=turn, binding=binding, data=DATA, filename='report.html', content_type='text/html', operation_id='stable-op')
    with patch('backend.services.investigation_attachments.upload_slack_attachment', side_effect=uncertain) as upload:
        assert publish(store, repository, **args)['status'] == 'outcome_unknown'
        row = next(r for r in store.list_case_files('123') if r['source_system'] == 'hermes')
        assert row['state'] == 'pending'
        generated = {**FILE, 'id': 'GENERATED', 'name': row['managed_filename'], 'mimetype': 'text/html', 'size': len(DATA)}
        with patch('backend.services.investigation_attachments._slack', return_value={'file': generated}):
            assert publish(store, repository, **args)['file']['state'] == 'available'
            assert publish(store, repository, **args)['replayed']
        assert upload.call_count == 1
    def receipt(**kwargs):
        kwargs['persist_file_id']('ZAC-FILE')
        return {'file_id': 'ZAC-FILE'}
    def info(method, payload):
        row = next(r for r in store.list_case_files('123') if r['source_system'] == 'zac')
        return {'file': {**generated, 'id': 'ZAC-FILE', 'name': row['managed_filename']}}
    with patch('backend.services.investigation_attachments.upload_slack_attachment', side_effect=receipt), patch('backend.services.investigation_attachments._slack', side_effect=info):
        acquire = Mock(return_value={'data': DATA, 'filename': 'report.html', 'content_type': 'text/html'})
        result = import_zac_file(store, repository, case_id='123', session_id='zac-synthetic',
            message_id='zac-message', source_file_id='zac-source', acquire=acquire)
        acquire.assert_called_once_with('zac-synthetic', 'zac-message', 'zac-source')
    assert result['file']['source_session_id'] == 'zac-synthetic'
    assert store.get_hermes_case_binding('123')['zac_session_id'] == 'zac-synthetic'


def test_real_processor_injects_private_work_scope(route_store, monkeypatch):
    from backend.services.automation_hermes_agent import HermesAgentTurnProcessor
    from backend.services.automation_hermes_tools import tool_save_investigation_progress, tool_escalate_human
    from backend.services.automation_ecs_contracts import JobKind
    from backend.tests.test_hermes_zendesk_agent import FakeHermesClient
    finish_initial(route_store)
    route_store.bind_hermes_case_thread('123', channel_id='C-TEST', thread_ts='123.45')
    monkeypatch.setenv('INVESTIGATION_CASE_FILES_ENABLED', '1')
    monkeypatch.setenv('HERMES_AGENT_API_TOKEN', 'synthetic-case-file-signing-key')
    monkeypatch.setenv('ENGINEER_SLACK_BOT_ID', 'B08RHT5TZV2')
    with patch('backend.services.investigation_attachments._slack', return_value={'file': {**FILE, 'name': 'rtc.log', 'size': len(DATA)}}), patch('backend.services.investigation_attachments._download', side_effect=transport()):
        receipt = send(route_store)
    job = route_store.claim_job(JobKind.AGENT_TURN, worker_id='file-test', lease_seconds=300)
    assert job.payload['turn_id'] == receipt['turn_id']
    scopes = []
    class ScopedClient(FakeHermesClient):
        def start_run(self, *, supportportal_file_scope=None, **kwargs):
            scopes.append(supportportal_file_scope)
            assert supportportal_file_scope['capability'] not in kwargs['input_text'] + kwargs['instructions']
            assert 'supportportal_files' in kwargs['enabled_toolsets']
            return super().start_run(**kwargs)
    def complete(_, key):
        assert key.endswith(':work')
        tool_escalate_human(route_store, None, turn_id=receipt['turn_id'], reason='engineer_discussion')
        tool_save_investigation_progress(route_store, None, turn_id=receipt['turn_id'],
            summary='Read sample', evidence=[], blockers=[], next_steps=[])
    with patch('backend.services.engineer_slack.notify_hermes_investigation_result'):
        result = HermesAgentTurnProcessor(route_store, client=ScopedClient(on_run_completed=complete),
            environment='preproduction', repository=None, poll_interval_seconds=.01).process(job)
    assert result['status'] == 'awaiting_investigation_review'
    assert len(scopes) == 1 and scopes[0]['turn_id'] == receipt['turn_id']
    assert scopes[0]['case_id'] == '123' and scopes[0]['phase'] == 'work'


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_actual_bridge_scope_tool_registry_download_path_and_publish_rejection(tmp_path, monkeypatch):
    context = _load(BUNDLE / 'supportportal_file_context.py', 'supportportal_file_context')
    bridge = _load(BUNDLE / 'case_files_bridge.py', 'case_files_bridge')
    assert not context.skill_allowed('support-case-files')
    assert not context.skill_allowed('bundle:support-case-files')
    assert not context.skill_allowed('tools/support-case-files')
    assert context.skill_allowed('argus')
    assert json.loads(bridge.handler('read')({'map_id': 1}))['code'] == 'case_file_scope_required'
    scope = {k: 'test' for k in ('namespace', 'case_id', 'turn_id', 'session_id', 'request_id', 'capability')}
    scope['phase'] = 'work'
    class Registry:
        def __init__(self): self.tools = []
        def register_tool(self, **tool): self.tools.append(tool)
    registry = Registry(); bridge.register(registry)
    assert len(registry.tools) == 5 and all(not t['check_fn']() for t in registry.tools)
    with context.bind_file_scope(scope, 'test', tmp_path):
        assert context.skill_allowed('support-case-files')
        assert all(t['check_fn']() for t in registry.tools)
        assert json.loads(bridge.handler('read')({'map_id': 1, 'turn_id': 'evil'}))['code'] == 'case_file_parameters_invalid'
        assert json.loads(bridge.handler('list')({'description': 'log'}))['code'] != 'case_file_parameters_invalid'
        assert json.loads(bridge.handler('publish')({'path': '/etc/hosts', 'operation_id': 'op'}))['code'] == 'case_file_path_outside_run'
        ref = {'managed_filename': '123-engineer-1.log', 'size_bytes': len(DATA)}
        response = io.BytesIO(DATA)
        response.headers = {'X-SupportPortal-File': json.dumps(ref)}
        monkeypatch.setenv('SUPPORTPORTAL_AGENT_API_BASE_URL', 'https://example.invalid/automation/preproduction')
        monkeypatch.setenv('SUPPORTPORTAL_AGENT_TOOL_TOKEN', 'synthetic-token')
        with patch.object(bridge.urllib.request, 'urlopen', return_value=response):
            result = json.loads(bridge.handler('download')({'map_id': 1}))
        target = Path(result['path']); assert target.read_bytes() == DATA
    assert not target.exists() and not context.skill_allowed('support-case-files')
    with context.bind_file_scope({**scope, 'phase': 'persona'}, 'test', tmp_path):
        assert not context.skill_allowed('support-case-files')
        assert all(not t['check_fn']() for t in registry.tools)


def test_streaming_tool_api_returns_exact_file_with_reference(bound):
    _, _, _, file = bound
    fid = bound[0].list_case_files('123')[0]['id']
    with patch('backend.services.investigation_attachments._slack', return_value={'file': file}), patch('backend.services.investigation_attachments._download', side_effect=transport()):
        response = api(bound, 'download_stream', map_id=fid)
    assert response.status_code == 200, response.text
    assert response.content == DATA
    assert json.loads(response.headers['X-SupportPortal-File'])['map_id'] == fid
    assert response.headers['cache-control'] == 'no-store'


def test_customer_transfer_uses_managed_name_and_confirms_map(bound, repository, monkeypatch):
    from backend.services.automation_native_notifications import deliver_customer_attachment
    store = bound[0]
    monkeypatch.setenv('ENGINEER_SLACK_CHANNEL_ID', 'C-TEST')
    parent = {'ticket_id': '123', 'comment_id': '55', 'channel_id': 'C-TEST', 'thread_ts': '123.45',
              'author_id': 'customer-test', 'turn_id': bound[2]['turn_id']}
    attachment = {'attachment_id': '99', 'file_name': 'rtc.log', 'size_bytes': len(DATA), 'content_type': 'text/plain'}
    def upload(**kwargs):
        row = next(r for r in store.list_case_files('123') if r['source_system'] == 'zendesk')
        assert kwargs['file_name'] == row['managed_filename']
        kwargs['persist_file_id']('CUSTOMER-FILE')
        return {'file_id': 'CUSTOMER-FILE'}
    def slack(method, payload):
        row = next(r for r in store.list_case_files('123') if r['source_system'] == 'zendesk')
        return {'file': {**bound[3], 'id': 'CUSTOMER-FILE', 'name': row['managed_filename']}}
    args = dict(scope=f'native-hermes-notification:{store.settings.job_namespace}', parent=parent, attachment=attachment,
                claim_token='claim', before_external=lambda: None, store=store)
    with patch('backend.services.investigation_attachments.download_zendesk_attachment', return_value=DATA), patch('backend.services.investigation_attachments.upload_slack_attachment', side_effect=upload) as submit, patch('backend.services.investigation_attachments._slack', side_effect=slack):
        assert deliver_customer_attachment(repository, **args)['status'] == 'delivered'
        assert deliver_customer_attachment(repository, **args)['file_id'] == 'CUSTOMER-FILE'
    assert submit.call_count == 1
    row = next(r for r in store.list_case_files('123') if r['source_system'] == 'zendesk')
    assert row['state'] == 'available' and row['provenance']['provider_id'] == 'customer-test'
    assert row['slack_filename'] == row['managed_filename']
    assert row['original_filename'] == 'rtc.log'


@pytest.mark.parametrize('failure', [None, 'deleted', 'stale'])
def test_selected_map_snapshot_through_draft_approval_and_worker(bound, failure):
    from types import SimpleNamespace
    from backend import worker
    from backend.repositories.ticket_repository import InMemoryTicketRepository
    from backend.services.automation_hermes_tools import tool_save_reply_draft, tool_save_investigation_progress
    from backend.services.automation_hermes_delivery import queue_hermes_draft_delivery
    from backend.services.account_zendesk_comments import normalize_snapshot
    from backend.tests.test_native_hermes_notifications import seed
    store, _, scope, file = bound
    tid = scope['turn_id']
    tool_save_investigation_progress(store, None, turn_id=tid, summary='Investigation ongoing', evidence=[], blockers=[], next_steps=[])
    store.complete_hermes_agent_turn(tid, result={'status': 'awaiting_investigation_review'})
    reply = store.create_investigation_reply_turn('123', source_turn_id=tid, base_event={})
    reply_id = reply['turn_id']
    store.start_hermes_agent_turn(reply_id, run_id=None)
    fid = store.list_case_files('123')[0]['id']
    persona = store.get_hermes_turn(reply_id)
    persona_binding = store.get_hermes_case_binding('123')
    persona_run = store.get_or_create_hermes_turn_run(reply_id, 'persona')
    persona_scope = run_scope(store, persona, persona_binding, persona_run['request_id'], 'persona')
    draft_body = {'turn_id': reply_id, 'file_scope': persona_scope['capability'],
                  'content': 'We are continuing the investigation.', 'selected_file_ids': [fid]}
    headers = {'Authorization': 'Bearer ' + store.settings.intake_shared_token}
    endpoint = '/automation/preproduction/v1/agent/tools/save_reply_draft'
    with patch('backend.automation_ecs_api._engineer_ticket_repository', return_value=None), patch('backend.services.automation_hermes_tools.run_engineer_guardrail_final', return_value={'decision': 'pass', 'blockers': []}):
        invalid = bound[1].post(endpoint, headers=headers, json={**draft_body, 'file_scope': scope['capability']})
        assert invalid.status_code == 422 and invalid.json()['detail']['code'] == 'file_scope_invalid'
        response = bound[1].post(endpoint, headers=headers, json=draft_body)
        assert response.status_code == 200, response.text
        saved = response.json()
        frozen = bound[1].post(endpoint, headers=headers, json={**draft_body, 'selected_file_ids': []})
        assert frozen.status_code == 422 and frozen.json()['detail']['code'] == 'draft_file_snapshot_conflict'

    draft = store.get_hermes_draft(saved['draft_id'])
    assert draft['basis']['attachments'][0]['map_id'] == fid
    assert draft['basis']['attachments'][0]['map_version'] == 1
    assert draft['basis']['attachments'][0]['file_name'] == store.get_case_file('123', fid)['managed_filename']
    store.request_hermes_draft_publish(draft['draft_id'])
    store.approve_hermes_case_draft(draft['draft_id'], approver='test')
    repo = InMemoryTicketRepository(); seed(repo)
    snapshot = normalize_snapshot({'source_updated_at': TIME, 'snapshot_complete': True, 'comments': [{
        'id': '55', 'public': True, 'author': {'role': 'end-user'}, 'body': 'Log', 'created_at': TIME}]})
    repo.sync_account_case_comments(ticket_id='123', account_case_id='AC-123', snapshot=snapshot, synced_at=TIME)
    queue_hermes_draft_delivery(store, repo, draft_id=draft['draft_id'], environment='preproduction')
    delivery = repo.list_account_zendesk_comment_deliveries(statuses=('queued',), limit=1)[0]
    assert delivery['immutable_content'] == draft['content']
    if failure == 'stale':
        store.complete_hermes_agent_turn(reply_id, result={'status': 'completed'})
        store.create_investigation_feedback_turn('123', feedback='New round', base_event={'source_event_id': 'new-round'})
        assert store.get_hermes_draft(draft['draft_id'])['status'] == 'stale'
    ownership = SimpleNamespace(comments_revision=snapshot.comments_revision, ticket_status='open', human_replied=False,
        unresolved_public_comment_id=None, assignee_id='777', group_id='1', ai_assignee_id='777', ai_group_id='1')
    from backend.services.engineer_slack import EngineerSlackDeliveryError
    with patch.object(worker, 'ticket_repository', repo), patch('backend.services.automation_ecs_store.create_automation_ecs_store', return_value=store), patch('backend.services.automation_ecs_runtime.AutomationEcsSettings.from_env', return_value=store.settings), patch.object(worker, 'read_ticket_ownership_snapshot', return_value=ownership), patch('backend.services.account_automation_ownership.read_ticket_ownership_snapshot', return_value=ownership), patch('backend.services.investigation_attachments._slack', side_effect=EngineerSlackDeliveryError('attachment_slack_file_not_found') if failure == 'deleted' else None, return_value={'file': file}), patch('backend.services.investigation_attachments._download', side_effect=transport()) as download, patch('backend.services.zendesk_comments.upload_ticket_attachment', return_value={'token': 'synthetic-upload', 'attachment_id': '201'}) as upload, patch.object(worker, 'add_ticket_comment', return_value=SimpleNamespace(comment_id='1001')) as comment:
        worker._deliver_hermes_zendesk_comment(delivery)
        worker._deliver_hermes_zendesk_comment(delivery)
        if failure is None:
            assert upload.call_count == comment.call_count == 1
            assert upload.call_args.kwargs['filename'] == draft['basis']['attachments'][0]['managed_filename']
            assert comment.call_args.kwargs['uploads'] == ['synthetic-upload']
        else:
            comment.assert_not_called()
            if failure == 'stale':
                download.assert_not_called()
    if failure == 'deleted':
        assert store.get_case_file('123', fid)['state'] == 'unavailable'
