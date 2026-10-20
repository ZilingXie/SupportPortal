"""Verify the merged case-files overlay tree (v2 three-way merge).

Checks: compile, marker presence (p2-203 preservation + R2 file capability),
skills_list/skill_view scope gates via real supportportal_file_context and
supportportal_skill_profile modules, session/case negatives.
"""
import ast
import json
import logging
import sys
from pathlib import Path

W = Path("/Users/xieziling/Desktop/personal_proj/cf-overlay-work")
TREE = W / "tree"
sys.path.insert(0, str(TREE / "hermes-agent"))

# ---------- 1. compile ----------
sources = [
    TREE / "hermes-agent/gateway/platforms/api_server_runs.py",
    TREE / "hermes-agent/tools/skills_tool.py",
    TREE / "hermes-agent/supportportal_file_context.py",
    TREE / "hermes-agent/supportportal_skill_profile.py",
    TREE / "deploy-ecs/supportportal_agent_tools/__init__.py",
    TREE / "deploy-ecs/supportportal_agent_tools/case_files_bridge.py",
]
for path in sources:
    compile(path.read_text(), str(path), "exec")
print("compile=passed")

# ---------- 2. markers ----------
gw = (TREE / "hermes-agent/gateway/platforms/api_server_runs.py").read_text()
assert gw.count("bind_profile(body.get") == 2, "bind_profile sites"
assert "validate_profile" in gw and "bind_file_scope" in gw, "gateway profile/file hooks"
assert "failure_reason" in gw, "p2-188 failure_reason preserved"

st = (TREE / "hermes-agent/tools/skills_tool.py").read_text()
assert st.count("skill_allowed(s[\"name\"])") == 3, "skill_allowed filters"
assert "skill_allowed(name)" in st, "skill_view gate"
assert st.count("profile_view") >= 3, "p2-203 profile stack preserved"

pl = (TREE / "deploy-ecs/supportportal_agent_tools/__init__.py").read_text()
assert "support_zac_consult" in pl, "zac consult preserved"
assert '"supportportal_investigation"' in pl and '"supportportal_persona"' in pl, "toolsets preserved"
assert 'TOOLSETS["supportportal_files"]' in pl, "files toolset added"
assert "register_files" in pl and "selected_file_ids" in pl, "file bridge wired"
yml = (TREE / "deploy-ecs/supportportal_agent_tools/plugin.yaml").read_text()
assert yml.count("support_case_files_") == 5, "yaml provides_tools"
assert (TREE / "investigation-profiles/supportportal-investigation-v1/supportportal-investigation/SKILL.md").is_file()
print("markers=passed")

# ---------- 3. gates ----------
import supportportal_file_context as fctx

tree = ast.parse((TREE / "hermes-agent/tools/skills_tool.py").read_text())
functions = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in {"skills_list", "skill_view"}]
assert {f.name for f in functions} == {"skills_list", "skill_view"}, "entry functions found"
module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), *functions], type_ignores=[])
ast.fix_missing_locations(module)
globals_ = {"json": json, "logger": logging.getLogger("verify"),
            "_skills_dir": lambda: TREE,
            "_find_all_skills": lambda: [{"name": "support-case-files"}, {"name": "argus"}],
            "_sort_skills": lambda skills: skills,
            "tool_error": lambda error, **kw: json.dumps({"error": error, **kw})}
exec(compile(module, "merged-skill-entry-functions", "exec"), globals_)

# No scope: file skill hidden from list, view denied.
assert [s["name"] for s in json.loads(globals_["skills_list"]())["skills"]] == ["argus"], "list hides file skill"
for name in ["support-case-files", "bundle:support-case-files", "technical/support-case-files"]:
    assert json.loads(globals_["skill_view"](name))["error"] == "case_file_scope_required", f"view gate {name}"

identity = {k: "test" for k in ("namespace", "case_id", "turn_id", "session_id", "request_id", "capability")}
identity["phase"] = "work"
with fctx.bind_file_scope(identity, "test", TREE):
    listed = [s["name"] for s in json.loads(globals_["skills_list"]())["skills"]]
    assert listed == ["support-case-files", "argus"], f"scoped list {listed}"
assert [s["name"] for s in json.loads(globals_["skills_list"]())["skills"]] == ["argus"], "scope exited"

# Wrong session cannot acquire the skill scope.
with fctx.bind_file_scope(identity, "foreign-session", TREE):
    assert json.loads(globals_["skill_view"]("support-case-files"))["error"] == "case_file_scope_required", "session negative"

# Non-work phase cannot see the file skill even with a bound scope.
persona_scope = dict(identity, phase="persona")
with fctx.bind_file_scope(persona_scope, "test", TREE):
    assert json.loads(globals_["skill_view"]("support-case-files"))["error"] == "case_file_scope_required", "phase negative"

# ---------- 4. zac preservation wiring ----------
import supportportal_skill_profile as prof
assert prof.PROFILE_ID == "supportportal-investigation-v1"
# The module hardcodes the container path; point it at the tree copy for host tests.
try:
    prof.PROFILE_ROOT = TREE / "does-not-exist"
    prof.validate_profile({"id": prof.PROFILE_ID, "allow_close": False, "turn_id": "t1", "request_id": "hmreq:t1:work"})
    raise AssertionError("profile validated without files?!")
except ValueError as exc:
    assert str(exc) == "supportportal_skill_profile_missing" or "missing" in str(exc), str(exc)
# With the real profile root present the profile validates and binds.
prof.PROFILE_ROOT = TREE / "investigation-profiles"
with prof.bind_profile({"id": prof.PROFILE_ID, "allow_close": False, "turn_id": "t1", "request_id": "hmreq:t1:work"}, run_id="r1") :
    scope = prof.active_profile()
    assert scope and scope["run_id"] == "r1" and "supportportal-investigation" in scope["names"]
    assert prof.skill_visible("supportportal-investigation") and not prof.skill_visible("argus")
assert prof.active_profile() is None, "profile scope exited"
print("gates=passed zac_profile=passed")
print("VERIFY_OK")
