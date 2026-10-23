from __future__ import annotations

import copy
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOTS = ROOT / 'docs/integrations/n8n/workflows'
RUNNER = Path(__file__).parent / 'fixtures/n8n_human_file_share_runner.cjs'


@pytest.fixture
def workflow():
    return json.loads((SNAPSHOTS / 'drafts/r1HIW8UNuCabiOPn.draft.json').read_text())['workflow']


@pytest.fixture
def sample():
    file = {'id': 'FSYNTHETIC1', 'name': 'synthetic.log', 'mimetype': 'text/plain', 'size': 42, 'user': 'USYNTHETIC1'}
    event = {'type': 'message', 'subtype': 'file_share', 'channel': 'C0BS0N61D1R',
             'thread_ts': '1700000000.000001', 'ts': '1700000001.000002', 'user': 'USYNTHETIC1',
             'text': '<@U08RVQSJQF2> synthetic description', 'files': [file]}
    message = copy.deepcopy(event)
    message.pop('channel')
    return {'input': event, 'responses': {
        'Verify Slack Credential Workspace': {'ok': True, 'team_id': 'T1CBEDLJY'},
        'Read Human Slack Channel': {'ok': True, 'channel': {'id': 'C0BS0N61D1R', 'context_team_id': 'T1CBEDLJY'}},
        'Read Human Slack Message': {'ok': True, 'messages': [message], 'has_more': False},
        'Resolve Hermes Attachment Binding': {'status': 'bound', 'zendesk_ticket_id': 'SYNTHETIC'},
        'Resolve Active Binding1': {'status': 'ignored_unbound'},
        'Resolve Hermes Binding': {'status': 'bound', 'zendesk_ticket_id': 'SYNTHETIC'},
        'Send Hermes Message': {'status': 'mocked_no_network'},
        'Send Message To SupportPortal1': {'status': 'mocked_no_network'},
        'Send Adhoc Session': {'status': 'mocked_no_network'},
    }}


def run(workflow, sample):
    result = subprocess.run(['node', str(RUNNER)], input=json.dumps({'workflow': workflow, **sample}),
                            text=True, capture_output=True, check=True)
    return json.loads(result.stdout)


def test_real_entry_normalizes_only_after_slack_verification(workflow, sample):
    result = run(workflow, sample)
    assert result['error'] is None
    assert len(result['writes']) == 1
    request = result['writes'][0]
    assert request['node'] == 'Send Hermes Message'
    assert '/preproduction/' in request['url']
    assert request['body']['team_id'] == 'T1CBEDLJY'
    assert request['body']['slack_user_id'] == 'USYNTHETIC1'
    assert request['body']['bot_id'] is None  # No fabricated bot provenance.
    assert request['body']['files'][0]['file_id'] == 'FSYNTHETIC1'
    assert request['body']['raw_text'] == sample['input']['text']
    assert request['body']['source_event_id'] == 'T1CBEDLJY:C0BS0N61D1R:1700000001.000002'
    slack = [r for r in result['requests'] if r['url'].startswith('https://slack.com/api/')]
    assert [r['url'].rsplit('/', 1)[1] for r in slack] == ['auth.test', 'conversations.info', 'conversations.replies']
    assert all(r['method'] == 'GET' for r in slack)
    assert slack[-1]['query'] == {'channel': 'C0BS0N61D1R', 'ts': sample['input']['thread_ts'],
                                 'oldest': sample['input']['ts'], 'latest': sample['input']['ts'],
                                 'inclusive': 'true', 'limit': '2'}


def test_verified_parent_client_and_canonical_metadata_are_preserved(workflow, sample):
    sample['input'].update(team='T1CBEDLJY', parent_user_id='UPARENT', client_msg_id='synthetic-client',
                           event_id='unverified-event-id', _verified=True)
    message = sample['responses']['Read Human Slack Message']['messages'][0]
    message.update(parent_user_id='UPARENT', client_msg_id='synthetic-client')
    message.pop('subtype')  # Real conversations.replies can omit event-only subtype.
    message['files'][0]['url_private_download'] = 'https://example.invalid/private'
    result = run(workflow, sample)
    assert result['error'] is None
    body = result['writes'][0]['body']
    assert body['parent_user_id'] == 'UPARENT'
    assert body['source_event_id'] == 'synthetic-client'
    assert 'unverified-event-id' not in json.dumps(body)
    assert 'url_private_download' not in json.dumps(result['outputs']['Verify Human Slack Source'])


@pytest.mark.parametrize('field,value', [
    ('team', 'TOTHER'), ('team', ''), ('channel', 'COTHER'), ('thread_ts', ''),
    ('ts', 'bad'), ('user', ''), ('user', 'U08RVQSJQF2'), ('bot_id', 'BOTHER'),
    ('bot_profile', {'id': 'BOTHER'}), ('subtype', 'message_changed'),
    ('text', 'no mention'), ('text', '<@U08RVQSJQF2>'), ('files', []),
    ('files', [{'id': None}]), ('files', [{'id': 'FSYNTHETIC1'}] * 11),
])
def test_malformed_or_out_of_scope_candidate_stops(workflow, sample, field, value):
    sample['input'][field] = value
    result = run(workflow, sample)
    assert result['writes'] == []
    assert 'Verify Slack Credential Workspace' not in result['visited']


@pytest.mark.parametrize('boundary,path,value', [
    ('Verify Slack Credential Workspace', ['ok'], False),
    ('Verify Slack Credential Workspace', ['team_id'], None),
    ('Verify Slack Credential Workspace', ['team_id'], 'TOTHER'),
    ('Read Human Slack Channel', ['ok'], False),
    ('Read Human Slack Channel', ['channel', 'id'], 'COTHER'),
    ('Read Human Slack Channel', ['channel', 'context_team_id'], None),
    ('Read Human Slack Channel', ['channel', 'context_team_id'], 'TOTHER'),
    ('Read Human Slack Message', ['ok'], False),
    ('Read Human Slack Message', ['has_more'], True),
    ('Read Human Slack Message', ['response_metadata'], {'next_cursor': 'remaining'}),
    ('Read Human Slack Message', ['messages'], []),
    ('Read Human Slack Message', ['messages', 0, 'ts'], '1700000001.000003'),
    ('Read Human Slack Message', ['messages', 0, 'thread_ts'], '1700000000.000002'),
    ('Read Human Slack Message', ['messages', 0, 'user'], 'UOTHER'),
    ('Read Human Slack Message', ['messages', 0, 'bot_id'], 'BOTHER'),
    ('Read Human Slack Message', ['messages', 0, 'bot_profile'], {'id': 'BOTHER'}),
    ('Read Human Slack Message', ['messages', 0, 'text'], '<@U08RVQSJQF2> different'),
    ('Read Human Slack Message', ['messages', 0, 'files'], []),
    ('Read Human Slack Message', ['messages', 0, 'files', 0, 'id'], 'FOTHER'),
    ('Read Human Slack Message', ['messages', 0, 'files', 0, 'user'], 'UOTHER'),
    ('Read Human Slack Message', ['messages', 0, 'files', 0, 'name'], 'other.log'),
    ('Read Human Slack Message', ['messages', 0, 'files', 0, 'mimetype'], 'application/pdf'),
    ('Read Human Slack Message', ['messages', 0, 'files', 0, 'size'], 50 * 1024 * 1024 + 1),
    ('Read Human Slack Message', ['messages', 0, 'files', 0, 'is_tombstoned'], True),
    ('Read Human Slack Message', ['messages', 0, 'files', 0, 'is_external'], True),
])
def test_untrusted_api_response_fails_before_binding(workflow, sample, boundary, path, value):
    target = sample['responses'][boundary]
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    result = run(workflow, sample)
    assert result['writes'] == []
    assert result['error'].startswith('human_file_source_unverified:')
    assert 'Resolve Hermes Attachment Binding' not in result['visited']


@pytest.mark.parametrize('boundary', ['Verify Slack Credential Workspace', 'Read Human Slack Channel', 'Read Human Slack Message'])
@pytest.mark.parametrize('error', ['Slack 403', 'Slack 429', 'timeout', 'malformed JSON'])
def test_transport_failures_never_forward(workflow, sample, boundary, error):
    sample['responses'][boundary] = {'__error': error}
    result = run(workflow, sample)
    assert result['writes'] == []
    assert result['error'] == error


@pytest.mark.parametrize('case', ['duplicate-message', 'duplicate-file', 'forged-parent', 'forged-client', 'spoofed-verification'])
def test_identity_ambiguity_and_injected_flags_cannot_bypass(workflow, sample, case):
    if case == 'duplicate-message':
        sample['responses']['Read Human Slack Message']['messages'] *= 2
    elif case == 'duplicate-file':
        sample['input']['files'] *= 2
        sample['responses']['Read Human Slack Message']['messages'][0]['files'] *= 2
    elif case == 'forged-parent':
        sample['input']['parent_user_id'] = 'UOTHER'
    elif case == 'forged-client':
        sample['input']['client_msg_id'] = 'forged'
    else:
        sample['input']['_verified'] = True
        sample['responses']['Verify Slack Credential Workspace']['team_id'] = 'TOTHER'
    result = run(workflow, sample)
    assert result['writes'] == []
    assert result['error'].startswith('human_file_source_unverified:')


@pytest.mark.parametrize('status', ['ignored_unbound', 'ignored_production'])
def test_files_never_fall_back_to_production_or_adhoc(workflow, sample, status):
    sample['responses']['Resolve Hermes Attachment Binding'] = {'status': status}
    result = run(workflow, sample)
    assert result['error'] is None
    assert result['writes'] == []
    assert result['visited'][-1] == 'Ignore Non-Investigation Attachment'


def test_bot_and_plain_text_paths_preserve_original_contract(workflow, sample):
    sample['input'].update(team='T1CBEDLJY', bot_id='B08RHT5TZV2', parent_user_id='UPARENT')
    bot = run(workflow, sample)
    assert bot['error'] is None
    assert bot['writes'][0]['body']['bot_id'] == 'B08RHT5TZV2'
    assert bot['writes'][0]['body']['parent_user_id'] == 'UPARENT'
    assert 'Verify Slack Credential Workspace' not in bot['visited']
    del sample['input']['parent_user_id']
    assert run(workflow, sample)['writes'] == []
    del sample['input']['bot_id']
    del sample['input']['subtype']
    sample['input']['files'] = []
    plain = run(workflow, sample)
    assert plain['error'] is None
    assert 'Resolve Active Binding1' in plain['visited']
    assert 'Verify Slack Credential Workspace' not in plain['visited']
    assert plain['writes'][0]['node'] == 'Send Hermes Message'
    sample['responses']['Resolve Active Binding1'] = {'status': 'bound', 'engineer_case_id': 'SYNTHETIC'}
    assert run(workflow, sample)['writes'][0]['node'] == 'Send Message To SupportPortal1'


def test_existing_nodes_unchanged_except_sender_source(workflow):
    published = json.loads((SNAPSHOTS / 'active/r1HIW8UNuCabiOPn.published.json').read_text())['workflow']
    by_name = {n['name']: n for n in workflow['nodes']}
    for node in published['nodes']:
        if node['name'] != 'Send Hermes Message':
            assert by_name[node['name']] == node
    for node in workflow['nodes']:
        if node['name'] in {'Verify Slack Credential Workspace', 'Read Human Slack Channel', 'Read Human Slack Message'}:
            assert node['onError'] == 'stopWorkflow'
            assert node['retryOnFail'] is False
            assert node['credentials']['slackApi']['id'] == 'UFeOoSgYxSmNuCnS'
            assert node['parameters']['options']['timeout'] == 10000


def test_carrier_detects_missing_verification_and_miswired_branch(workflow, sample):
    broken = copy.deepcopy(workflow)
    broken['connections']['Human File Share Candidate']['main'][0][0]['node'] = 'Resolve Hermes Attachment Binding'
    result = run(broken, sample)
    assert result['writes'] == []
    assert 'unexecuted node: Verify Human Slack Source' in result['error']
    broken = copy.deepcopy(workflow)
    broken['connections']['Verify Human Slack Source']['main'][0][0]['node'] = 'Send Adhoc Session'
    result = run(broken, sample)
    assert result['writes'] == []
    assert result['error'] == 'Attachments require a bound Investigation thread'
