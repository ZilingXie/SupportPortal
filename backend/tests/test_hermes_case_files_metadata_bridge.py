"""Registered Hermes bridge -> actual private tool API, synthetic storage only."""
import importlib.util
import io
import json
from pathlib import Path
import sys
import urllib.error
from unittest.mock import patch

import pytest

from backend.tests.test_investigation_case_files import bound, route_store, pg_store

BUNDLE = Path(__file__).resolve().parents[2] / 'deployment/hermes-case-files'
pytestmark = pytest.mark.parametrize('route_store', ['memory'], indirect=True)


def load(path, name, monkeypatch):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def entry(bound, monkeypatch, tmp_path):
    store, client, scope, _ = bound
    context = load(BUNDLE / 'supportportal_file_context.py', 'supportportal_file_context', monkeypatch)
    bridge = load(BUNDLE / 'case_files_bridge.py', 'metadata_bridge_test', monkeypatch)
    tools = {}

    class Registry:
        def register_tool(self, **tool):
            tools[tool['name']] = tool

    bridge.register(Registry())
    monkeypatch.setenv('SUPPORTPORTAL_AGENT_API_BASE_URL', 'https://supportportal.invalid/automation/preproduction')
    monkeypatch.setenv('SUPPORTPORTAL_AGENT_TOOL_TOKEN', store.settings.intake_shared_token)
    calls = []

    def transport(request, timeout):
        assert timeout == 120
        assert request.full_url.startswith('https://supportportal.invalid/automation/preproduction/v1/agent/tools/case_files_')
        payload = json.loads(request.data)
        calls.append(payload)
        path = request.full_url.removeprefix('https://supportportal.invalid')
        # Only the network boundary is replaced. Run the real HTTP handler,
        # capability checks and storage filters, including server refusals.
        with patch('backend.automation_ecs_api._engineer_ticket_repository', return_value=None):
            response = client.post(path, content=request.data, headers=dict(request.header_items()))
        if response.status_code >= 400:
            raise urllib.error.HTTPError(request.full_url, response.status_code, 'refused', {}, io.BytesIO(response.content))
        return io.BytesIO(response.content)

    monkeypatch.setattr(bridge.urllib.request, 'urlopen', transport)
    row = store.list_case_files('123')[0]
    store.update_case_file('123', row['id'], short_description='Engine TLS Straße failure')
    second = store.register_case_file(case_id='123', source_system='zendesk', source_file_id='customer-csv',
        original_filename='metrics.csv', content_type='text/csv', size_bytes=3,
        purpose='customer-report', short_description='Audio metrics',
        provenance={**row['provenance'], 'turn_id': 'other-turn', 'source_event_id': 'synthetic-customer-event'})
    store.update_case_file('123', second['id'], state='available', slack_file_id='F-CUSTOMER', slack_filename='metrics.csv')
    foreign = store.register_case_file(case_id='456', source_system='engineer', source_file_id='foreign-file',
        original_filename='foreign.log', content_type='text/plain', size_bytes=3,
        purpose='supplement', short_description='Engine TLS Straße failure',
        provenance={**row['provenance'], 'source_event_id': 'synthetic-foreign-event'})
    store.update_case_file('456', foreign['id'], state='available')
    return store, scope, context, tools, calls, row, second, foreign, tmp_path


def invoke(entry, action, args, scope=None, session=None):
    _, trusted, context, tools, _, _, _, _, tmp = entry
    tool = tools['support_case_files_' + action]
    with context.bind_file_scope(trusted if scope is None else scope, session or trusted['session_id'], tmp):
        return json.loads(tool['handler'](args))


@pytest.mark.parametrize('query,match', [('TLS', True), ('tls', True), ('STRASSE', True), ('video', False)])
def test_registered_list_description_matches_actual_metadata(entry, query, match):
    _, _, _, tools, calls, row, _, foreign, _ = entry
    schema = tools['support_case_files_list']['schema']['parameters']
    assert set(schema['properties']) == {'source', 'extension', 'purpose', 'introduced_turn_id', 'description'}
    assert schema['additionalProperties'] is False
    assert schema['properties']['description']['type'] == 'string'
    result = invoke(entry, 'list', {'description': query})
    assert [f['map_id'] for f in result['files']] == ([row['id']] if match else [])
    assert foreign['id'] not in [f['map_id'] for f in result['files']]
    if match:
        assert result['files'][0]['short_description'] == 'Engine TLS Straße failure'
    assert calls[-1]['description'] == query
    assert 'case_id' not in calls[-1]


def test_existing_list_filters_and_composition(entry):
    _, scope, _, _, _, row, second, _, _ = entry
    for filters, expected in [
        ({'source': 'engineer'}, [row['id']]),
        ({'source': 'zendesk'}, [second['id']]),
        ({'extension': '.LOG'}, [row['id']]),
        ({'purpose': 'customer-report'}, [second['id']]),
        ({'introduced_turn_id': scope['turn_id']}, [row['id']]),
        ({'description': 'tls', 'source': 'engineer', 'extension': '.LOG',
          'purpose': row['purpose'], 'introduced_turn_id': scope['turn_id']}, [row['id']]),
        ({'description': 'tls', 'source': 'zendesk'}, []),
    ]:
        result = invoke(entry, 'list', filters)
        assert 'files' in result, (filters, result)
        assert [f['map_id'] for f in result['files']] == expected


def test_missing_wrong_phase_and_wrong_session_scope_refuse_before_http(entry):
    _, scope, _, tools, calls, _, _, _, _ = entry
    tool = tools['support_case_files_list']
    assert not tool['check_fn']()
    assert json.loads(tool['handler']({'description': 'tls'})) == {'ok': False, 'code': 'case_file_scope_required'}
    assert invoke(entry, 'list', {}, scope={**scope, 'phase': 'persona'})['code'] == 'case_file_scope_required'
    assert invoke(entry, 'list', {}, session='foreign-session')['code'] == 'case_file_scope_required'
    assert calls == []


def test_cross_case_and_forged_capability_refused_by_actual_api(entry):
    _, scope, _, _, calls, _, _, foreign, _ = entry
    assert invoke(entry, 'read', {'map_id': foreign['id']}) == {'ok': False, 'code': 'case_file_not_found'}
    assert len(calls) == 1
    assert invoke(entry, 'list', {}, scope={**scope, 'capability': scope['capability'] + 'x'}) == {'ok': False, 'code': 'file_scope_invalid'}
    assert len(calls) == 2
    for args in [{'case_id': '456'}, {'short_description': 'tls'}, {'file_scope': 'forged'}]:
        assert invoke(entry, 'list', args) == {'ok': False, 'code': 'case_file_parameters_invalid'}
    assert len(calls) == 2


def test_old_schema_fault_is_detected_before_transport(entry, monkeypatch):
    bridge = sys.modules['metadata_bridge_test']
    monkeypatch.delitem(bridge.SCHEMAS['list']['properties'], 'description')
    assert invoke(entry, 'list', {'description': 'tls'}) == {'ok': False, 'code': 'case_file_parameters_invalid'}
    assert entry[4] == []
