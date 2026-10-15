"""Apply cloud patch in a disposable tree and exercise patched Skill entry gates.

Requires the local agent-infra source tree named by source-baseline.json.
Does not modify that tree or install any skill into a live Hermes home.
"""
import argparse
import ast
import importlib.util
import json
import logging
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
BUNDLE = ROOT / 'deployment/hermes-case-files'


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def verify(source):
    source = Path(source)
    baseline = json.loads((BUNDLE / 'source-baseline.json').read_text())
    with tempfile.TemporaryDirectory(prefix='case-files-cloud-patch-') as directory:
        target = Path(directory)
        for name in baseline['files']:
            dest = target / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / name, dest)
        load(BUNDLE / 'prepare.py', 'case_files_prepare').prepare(target)
        for name in ['hermes-agent/gateway/platforms/api_server_runs.py', 'hermes-agent/tools/skills_tool.py', 'deploy-ecs/supportportal_agent_tools/__init__.py']:
            compile((target / name).read_text(), name, 'exec')
        scope = load(target / 'hermes-agent/supportportal_file_context.py', 'supportportal_file_context')
        # Execute the patched public function bodies with their unchanged discovery
        # and parsing dependencies stubbed. Scope enforcement itself is not mocked.
        tree = ast.parse((target / 'hermes-agent/tools/skills_tool.py').read_text())
        functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in {'skills_list', 'skill_view'}]
        module = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), *functions], type_ignores=[])
        ast.fix_missing_locations(module)
        globals_ = {'json': json, 'logger': logging.getLogger(__name__), '_skills_dir': lambda: target,
                    '_find_all_skills': lambda: [{'name': 'support-case-files'}, {'name': 'argus'}],
                    '_sort_skills': lambda skills: skills, 'tool_error': lambda error, **kw: json.dumps({'error': error, **kw})}
        exec(compile(module, 'patched-skill-entry-functions', 'exec'), globals_)
        assert globals_['skills_list']() and [s['name'] for s in json.loads(globals_['skills_list']())['skills']] == ['argus']
        for name in ['support-case-files', 'bundle:support-case-files', 'technical/support-case-files']:
            assert json.loads(globals_['skill_view'](name))['error'] == 'case_file_scope_required'
        identity = {key: 'test' for key in ('namespace', 'case_id', 'turn_id', 'session_id', 'request_id', 'capability')}
        identity['phase'] = 'work'
        with scope.bind_file_scope(identity, 'test', target):
            assert [s['name'] for s in json.loads(globals_['skills_list']())['skills']] == ['support-case-files', 'argus']
        assert [s['name'] for s in json.loads(globals_['skills_list']())['skills']] == ['argus']
        # Negative carrier: a different session must not acquire the Skill scope.
        with scope.bind_file_scope(identity, 'foreign-session', target):
            assert json.loads(globals_['skill_view']('support-case-files'))['error'] == 'case_file_scope_required'
        print('patch_apply=passed core_compile=passed skills_list_gate=passed skill_view_gate=passed session_negative=passed')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('agent_infra_source')
    verify(parser.parse_args().agent_infra_source)
