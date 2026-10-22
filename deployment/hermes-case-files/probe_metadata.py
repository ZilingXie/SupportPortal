"""Read-only Hermes metadata/Skill probe. No session, run or backend tool calls.

Run with the image's Python: probe_metadata.py [--root /opt/hermes]
Use --require-description only for the candidate/new deployment.
The synthetic scope cannot authenticate to SupportPortal and stays process-local.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys


def probe(root, require_description=False):
    sys.path.insert(0, str(root))
    import supportportal_file_context as context
    import supportportal_skill_profile as profile
    from tools.skills_tool import skills_list, skill_view

    bridge_path = root / 'plugins/supportportal_agent_tools/case_files_bridge.py'
    spec = importlib.util.spec_from_file_location('metadata_readiness_bridge', bridge_path)
    bridge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bridge)
    registered = {}

    class Registry:
        def register_tool(self, **tool):
            registered[tool['name']] = tool

    bridge.register(Registry())
    listing = registered['support_case_files_list']
    schema = listing['schema']['parameters']
    assert schema['additionalProperties'] is False
    assert not listing['check_fn']()
    identity = {k: 'metadata-probe-no-authority' for k in
                ('namespace', 'case_id', 'turn_id', 'session_id', 'request_id', 'capability')}
    identity['phase'] = 'work'
    config = {'id': 'supportportal-investigation-v1', 'allow_close': False,
              'turn_id': identity['turn_id'], 'request_id': 'hmreq:' + identity['turn_id'] + ':work'}

    with profile.bind_profile(config, run_id='metadata-readonly-probe'):
        denied = json.loads(skill_view('support-case-files', preprocess=False))
        assert denied.get('error') == 'skill_profile_denied', denied.get('error')
        with context.bind_file_scope(identity, identity['session_id'], ''):
            assert listing['check_fn']()
            visible = json.loads(skills_list())
            names = [s['name'] for s in visible['skills']]
            assert 'support-case-files' in names, names
            viewed = json.loads(skill_view('support-case-files', preprocess=False))
            assert viewed.get('success') is True, viewed.get('error')
        with context.bind_file_scope({**identity, 'phase': 'persona'}, identity['session_id'], ''):
            assert not listing['check_fn']()
            assert 'support-case-files' not in [s['name'] for s in json.loads(skills_list())['skills']]
    skill = root / 'case-files-skill/SKILL.md'
    text = skill.read_text()
    result = {
        'schema_properties': sorted(schema['properties']),
        'schema': schema,
        'skill_has_short_description': 'short_description' in text,
        'profile_combined_work_list_and_view': True,
        'profile_only_and_persona_denied': True,
        'visible_work_skills': names,
        'asset_sha256': {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
                         for p in (bridge_path, skill, root / 'supportportal_file_context.py',
                                   root / 'supportportal_skill_profile.py', root / 'tools/skills_tool.py',
                                   root / 'gateway/platforms/api_server_runs.py')},
        'sessions_created': False,
        'backend_tools_called': False,
    }
    if require_description:
        assert 'description' in schema['properties'], 'description_schema_missing'
        assert result['skill_has_short_description'], 'short_description_skill_missing'
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('/opt/hermes'))
    parser.add_argument('--require-description', action='store_true')
    args = parser.parse_args()
    print('METADATA_PROBE_JSON=' + json.dumps(probe(args.root, args.require_description), sort_keys=True))
