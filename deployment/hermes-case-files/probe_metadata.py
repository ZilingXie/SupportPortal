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


APPROVED_SKILL_SHA256 = 'bce133eb503042e75f67b7d512877fa4a2efccc2af75766c2fe15a00b330154a'
EFS_SKILL_PATH = Path('/opt/data/skills/support-case-files/SKILL.md')


def probe(root, require_description=False, expected_served_path=EFS_SKILL_PATH,
          expected_served_sha256=APPROVED_SKILL_SHA256):
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
    assert json.loads(skill_view('support-case-files', preprocess=False)).get('error') in {
        'case_file_scope_required', 'skill_profile_denied'}
    identity = {k: 'metadata-probe-no-authority' for k in
                ('namespace', 'case_id', 'turn_id', 'session_id', 'request_id', 'capability')}
    identity['phase'] = 'work'
    config = {'id': 'supportportal-investigation-v1', 'allow_close': False,
              'turn_id': identity['turn_id'], 'request_id': 'hmreq:' + identity['turn_id'] + ':work'}

    with profile.bind_profile(config, run_id='metadata-readonly-probe'):
        denied = json.loads(skill_view('support-case-files', preprocess=False))
        assert denied.get('error') == 'skill_profile_denied', denied.get('error')
        assert 'support-case-files' not in [s['name'] for s in json.loads(skills_list())['skills']]
        with context.bind_file_scope(identity, identity['session_id'], ''):
            assert listing['check_fn']()
            visible = json.loads(skills_list())
            names = [s['name'] for s in visible['skills']]
            assert 'support-case-files' in names, names
            viewed = json.loads(skill_view('support-case-files', preprocess=False))
            assert viewed.get('success') is True, viewed.get('error')
            served_content = viewed.get('content', '')
            served_path = viewed.get('_source_path')
            assert isinstance(served_content, str), 'invalid_served_skill_content'
            assert isinstance(served_path, str), 'actual_served_skill_path_missing'
        with context.bind_file_scope({**identity, 'phase': 'persona'}, identity['session_id'], ''):
            assert not listing['check_fn']()
            assert 'support-case-files' not in [s['name'] for s in json.loads(skills_list())['skills']]
            assert json.loads(skill_view('support-case-files', preprocess=False)).get('error') in {
                'case_file_scope_required', 'skill_profile_denied'}
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
        'served_source_path': served_path,
        'served_resolved_path': str(Path(served_path).resolve()),
        'served_content_sha256': hashlib.sha256(served_content.encode()).hexdigest(),
        'served_has_short_description': 'short_description' in served_content,
    }
    if require_description:
        assert 'description' in schema['properties'], 'description_schema_missing'
        assert result['skill_has_short_description'], 'short_description_skill_missing'
        assert result['served_has_short_description'], 'actual_served_skill_metadata_missing'
        path = Path(served_path)
        assert path == expected_served_path and path.resolve() == expected_served_path, 'actual_served_skill_path_mismatch'
        assert not path.is_symlink(), 'actual_served_skill_symlink'
        assert result['served_content_sha256'] == expected_served_sha256, 'actual_served_skill_hash_mismatch'
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected_served_sha256, 'actual_efs_skill_hash_mismatch'
        assert hashlib.sha256(skill.read_bytes()).hexdigest() == expected_served_sha256, 'bundled_skill_hash_mismatch'
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('/opt/hermes'))
    parser.add_argument('--require-description', action='store_true')
    parser.add_argument('--expected-served-path', type=Path, default=EFS_SKILL_PATH)
    parser.add_argument('--expected-served-sha256', default=APPROVED_SKILL_SHA256)
    args = parser.parse_args()
    result = probe(args.root, args.require_description, args.expected_served_path, args.expected_served_sha256)
    print('METADATA_PROBE_JSON=' + json.dumps(result, sort_keys=True))
