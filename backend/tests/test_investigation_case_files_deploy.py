"""Feature switch through the real release shell → task renderer; no AWS calls."""
import json
import os
from pathlib import Path
import subprocess
import sys
import shlex

import pytest

from backend.tests.test_automation_ecs_deploy import _task_definition, _as_preproduction, _manifest
from backend.scripts.automation_ecs_deploy import render_task_definition

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize('switch,expected', [('preserve', '0'), ('enabled', '1'), ('disabled', '0')])
def test_release_shell_passes_case_file_switch_to_real_cli(tmp_path, switch, expected):
    current = _task_definition(tmp_path, 'worker')
    _as_preproduction(current)
    manifest = _manifest(tmp_path)
    output = tmp_path / 'rendered.json'
    source = (ROOT / 'deployment/deploy_automation_ecs_release.sh').read_text()
    start = source.index('render_role_task_definition() {')
    end = source.index('\n}\n', start) + 3
    function = source[start:end].replace(">/dev/null", "")
    script = 'set -euo pipefail\nfail() { echo "$1" >&2; exit 1; }\n' + function + '\nrender_role_task_definition worker ' + shlex.quote(str(current)) + ' ' + shlex.quote(str(output))
    env = {**os.environ, 'PYTHON_BIN': sys.executable, 'MANIFEST_PATH': str(manifest),
        'REGISTRY_ID': '123456789012', 'REGION': 'us-east-1', 'ENVIRONMENT': 'preproduction',
        'PROMOTION_REPOSITORY': 'supportportal/preproduction', 'HERMES_CASE_WORKFLOW_MODE': '',
        'KNOWLEDGE_STAGE_CONFIG': '', 'HERMES_PERSONA_ENABLED': '0', 'AUTOMATION_CASE_ENGINE': 'legacy',
        'HERMES_AGENT_ENABLED': '0', 'ENABLEMENT_WORKFLOW_MODE': 'manual', 'AGENT_MODEL_VALUE': 'test-model',
        'INVESTIGATION_CASE_FILES_SWITCH': switch}
    result = subprocess.run(['bash'], input=script, text=True, env=env, cwd=ROOT, capture_output=True)
    assert result.returncode == 0, result.stderr + result.stdout
    values = {v['name']: v['value'] for v in json.loads(output.read_text())['containerDefinitions'][0]['environment']}
    assert values['INVESTIGATION_CASE_FILES_ENABLED'] == expected


def test_renderer_preserves_existing_switch_and_rejects_production_enable(tmp_path):
    current = _task_definition(tmp_path, 'worker')
    _as_preproduction(current)
    payload = json.loads(current.read_text())
    payload['taskDefinition']['containerDefinitions'][0]['environment'].append({'name': 'INVESTIGATION_CASE_FILES_ENABLED', 'value': '1'})
    current.write_text(json.dumps(payload))
    args = dict(role='worker', current_path=current, manifest_path=_manifest(tmp_path), registry_id='123456789012',
                region='us-east-1', environment='preproduction', repository='supportportal/preproduction', agent_model='test-model')
    rendered = render_task_definition(**args)
    assert next(v['value'] for v in rendered['containerDefinitions'][0]['environment'] if v['name'] == 'INVESTIGATION_CASE_FILES_ENABLED') == '1'
    with pytest.raises(ValueError, match='only be enabled in Preproduction'):
        render_task_definition(**{**args, 'environment': 'production', 'repository': 'supportportal/production', 'agent_model': None}, investigation_case_files_enabled=True)
