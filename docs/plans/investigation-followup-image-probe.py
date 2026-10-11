"""Run inside the released worker image; every external write is mocked."""
import json
import os
import urllib.error
from types import SimpleNamespace
from unittest.mock import Mock, patch

from backend.repositories.ticket_repository import InMemoryTicketRepository
from backend.services.investigation_attachments import download_zendesk_attachment
from backend.services.automation_native_notifications import (
    _attachment_failure, _retryable_attachment_result, attachment_recovery_blocker,
    deliver_native_notification, TRANSLATION_UNAVAILABLE,
)
from backend.services.engineer_slack import _message_payload

checks = []
def response(data):
    r = Mock()
    r.__enter__ = Mock(return_value=r)
    r.__exit__ = Mock(return_value=False)
    r.read.return_value = data
    return r

opener = Mock()
opener.open.side_effect = [urllib.error.HTTPError('https://agoraio.zendesk.com/file', 302,
    'redirect', {'Location': 'https://p.zdusercontent.com/signed'}, None), response(b'png')]
comment = response(json.dumps({'comments': [{'id': 55, 'public': True,
    'attachments': [{'id': 99, 'content_url': 'https://agoraio.zendesk.com/file'}]}]}).encode())
with patch('backend.services.investigation_attachments._basic_auth_header', return_value='Basic fixture'), patch('backend.services.investigation_attachments.urllib.request.urlopen', return_value=comment) as api, patch('backend.services.investigation_attachments.urllib.request.build_opener', return_value=opener):
    assert download_zendesk_attachment(ticket_id='123', comment_id='55', attachment_id='99') == b'png'
requests = [api.call_args.args[0], *[c.args[0] for c in opener.open.call_args_list]]
assert all(r.get_header('User-agent') == 'SupportPortal-AttachmentSync/1.0' for r in requests)
assert requests[2].get_header('Authorization') is None
checks.append('authenticated_comment_cdn_ua_auth_strip')

for status, retry in [(403, False), (429, True), (503, True)]:
    assert _attachment_failure(urllib.error.HTTPError('https://fixture.invalid', status, 'error', {}, None), stage='download')['retryable'] is retry
assert not _retryable_attachment_result({'failure_code': '403', 'retryable': True})
checks.append('http_error_and_historical_retry_classification')

binding = {'direction': 'pending', 'case_task': {'direction': 'investigation'},
    'slack_channel_id': 'C-FIXTURE', 'slack_thread_ts': '123.45'}
parent = {'ticket_id': '123', 'channel_id': 'C-FIXTURE', 'thread_ts': '123.45'}
store = SimpleNamespace(get_hermes_case_binding=lambda _: binding,
    get_case_mirror=lambda _: {'ticket': {'status': 'open'}})
assert attachment_recovery_blocker(store, parent) is None
binding['direction'] = 'human'
assert attachment_recovery_blocker(store, parent) == 'actual_human_takeover'
checks.append('fixed_pending_investigation_and_human_fence')

repo = InMemoryTicketRepository()
os.environ['ENGINEER_SLACK_CHANNEL_ID'] = 'C-FIXTURE'
os.environ['ENGINEER_SLACK_OUTBOUND_ENABLED'] = '1'
scope = 'native-hermes-notification:fixture'
body = '客户续轮 <@U-FIXTURE> close the case'
for fail in (False, True):
    key = f'comment:123:{fail}'
    repo.enqueue_native_notification(scope=scope, key=key, created_at='2026-10-11T00:00:00Z', payload={
        'kind': 'customer_comment', **parent, 'comment_id': str(fail), 'turn_id': 'turn-fixture',
        'body': body, 'author_type': 'customer', 'source_updated_at': '2026-10-11T00:00:00Z'})
    with patch('backend.services.automation_hermes_delivery.translate_draft_for_delivery',
        side_effect=RuntimeError('fixture translation unavailable') if fail else None,
        return_value=SimpleNamespace(translated_text='Customer follow-up <@U-FIXTURE> close the case')), patch('backend.services.automation_native_notifications.post_engineer_slack_event',
        return_value={'status': 'delivered', 'slack_channel_id': 'C-FIXTURE', 'slack_message_ts': '124.01'}) as post:
        assert deliver_native_notification(repo, scope=scope, key=key, claim_token='fixture', before_external=lambda: None)['status'] == 'confirmed'
        assert deliver_native_notification(repo, scope=scope, key=key, claim_token='fixture', before_external=lambda: None)['replayed']
    post.assert_called_once()
    sent = post.call_args.args[0]
    assert sent['plain_text_sections'][-1] == (TRANSLATION_UNAVAILABLE if fail else 'Customer follow-up <@U-FIXTURE> close the case')
    assert all(b['text']['type'] == 'plain_text' for b in _message_payload(sent, thread_ts='123.45')['blocks'])
    assert repo.get_native_notification(scope, key)['response_payload']['body'] == body
checks.append('english_notification_fallback_plaintext_original_receipt')
print(json.dumps({'status': 'passed', 'checks': checks, 'business_writes': 0,
    'build_ref': os.environ.get('APP_BUILD_REF')}))
