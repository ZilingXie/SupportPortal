"""R2-V2-B1 regression: combined profile + file-scope skill visibility."""
import ast
import json
import logging
import sys
from pathlib import Path

TREE = Path("/tmp/tree-b1")
sys.path.insert(0, str(TREE / "hermes-agent"))

# ---------- compile ----------
for rel in ("hermes-agent/gateway/platforms/api_server_runs.py",
            "hermes-agent/tools/skills_tool.py",
            "hermes-agent/supportportal_file_context.py",
            "hermes-agent/supportportal_skill_profile.py",
            "deploy-ecs/supportportal_agent_tools/__init__.py",
            "deploy-ecs/supportportal_agent_tools/case_files_bridge.py"):
    compile((TREE / rel).read_text(), rel, "exec")
print("compile=passed")

import supportportal_file_context as fctx
import supportportal_skill_profile as prof
prof.PROFILE_ROOT = TREE / "investigation-profiles"

# ---------- file_skill_visible unit ----------
assert fctx.file_skill_visible("support-case-files") is False  # no scope
assert fctx.file_skill_visible("argus") is False
identity = {k: "test" for k in ("namespace", "case_id", "turn_id", "session_id", "request_id", "capability")}
with fctx.bind_file_scope(dict(identity, phase="work"), "test", TREE):
    assert fctx.file_skill_visible("support-case-files") is True
    assert fctx.file_skill_visible("argus") is False
    assert fctx.file_skill_visible("bundle:support-case-files") is True
with fctx.bind_file_scope(dict(identity, phase="persona"), "test", TREE):
    assert fctx.file_skill_visible("support-case-files") is False
print("file_skill_visible=passed")

# ---------- extract skills_list / skill_view / wrapper ----------
tree = ast.parse((TREE / "hermes-agent/tools/skills_tool.py").read_text())
names = {"skills_list", "skill_view", "_skill_view_with_bump", "_supportportal_listing_visible"}
functions = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
assert {f.name for f in functions} == names, "entry functions found"
module = ast.Module(body=[ast.ImportFrom(module="__future__",
    names=[ast.alias(name="annotations")], level=0), *functions], type_ignores=[])
ast.fix_missing_locations(module)
predicate_box = {}
def _stub_find_all_skills():
    # Mirror the REAL _find_all_skills listing filter through the same shared
    # predicate the merged file applies at its three return sites, so the
    # offline harness cannot hide a listing-filter regression again.
    fixture = [{"name": "support-case-files"}, {"name": "argus"}]
    return [dict(s) for s in fixture if predicate_box["p"](s["name"])]
globals_ = {"json": json, "logger": logging.getLogger("verify-b1"),
            "_skills_dir": lambda: TREE,
            "_find_all_skills": _stub_find_all_skills,
            "_sort_skills": lambda skills: skills,
            "tool_error": lambda error, **kw: json.dumps({"error": error, **kw}),
            "_check_skill_view_dedup": lambda *a, **k: None,
            "skill_view": lambda *a, **k: json.dumps({"success": True, "via": "inner"}),
            "_skill_lookup_path_error": lambda name: None}
exec(compile(module, "b1-skill-entry-functions", "exec"), globals_)
skills_list, skill_view, wrapper = globals_["skills_list"], globals_["skill_view"], globals_["_skill_view_with_bump"]
predicate_box["p"] = globals_["_supportportal_listing_visible"]

PROFILE = {"id": "supportportal-investigation-v1", "allow_close": False,
           "turn_id": "t1", "request_id": "hmreq:t1:work"}
WORK = dict(identity, phase="work")
PERSONA = dict(identity, phase="persona")

def names_from_listing(result):
    return [s["name"] for s in json.loads(result)["skills"]]

# 1. Profile only: unchanged p2-203 behavior.
with prof.bind_profile(PROFILE, run_id="r1"):
    listed = names_from_listing(skills_list())
    assert listed == ["hermes-zac-engineering-handoff", "supportportal-investigation"], listed
    assert json.loads(skill_view("support-case-files"))["error"] == "skill_profile_denied"
    assert json.loads(wrapper({"name": "support-case-files", "file_path": None}))["error"] == "skill_profile_denied"
print("profile_only=passed")

# 2. Combined (reviewer repro): profile + work file scope -> file skill listed + viewable gates.
with prof.bind_profile(PROFILE, run_id="r1"), fctx.bind_file_scope(WORK, "test", TREE):
    listed = names_from_listing(skills_list())
    assert listed == ["hermes-zac-engineering-handoff", "supportportal-investigation", "support-case-files"], listed
    view = json.loads(skill_view("support-case-files"))
    assert view.get("error") != "skill_profile_denied", view
    assert view.get("error") != "case_file_scope_required", view
    wv = json.loads(wrapper({"name": "support-case-files", "file_path": None}))
    assert wv.get("error") != "skill_profile_denied", wv
    # non-profile, non-file skills stay denied under the profile
    assert json.loads(skill_view("argus"))["error"] == "skill_profile_denied"
    # profile entries remain viewable through profile_view
    pv = json.loads(skill_view("supportportal-investigation"))
    assert pv.get("success") is True, pv
print("combined=passed")

# 3. Combined with persona-phase file scope: file skill hidden again.
with prof.bind_profile(PROFILE, run_id="r1"), fctx.bind_file_scope(PERSONA, "test", TREE):
    listed = names_from_listing(skills_list())
    assert listed == ["hermes-zac-engineering-handoff", "supportportal-investigation"], listed
    assert json.loads(skill_view("support-case-files"))["error"] == "skill_profile_denied"
print("combined_persona_phase=passed")

# 4. File scope only: unchanged R2 behavior.
with fctx.bind_file_scope(WORK, "test", TREE):
    listed = names_from_listing(skills_list())
    assert listed == ["support-case-files", "argus"], listed
assert names_from_listing(skills_list()) == ["argus"]
view = json.loads(skill_view("support-case-files"))
assert view["error"] == "case_file_scope_required", view
with fctx.bind_file_scope(PERSONA, "test", TREE):
    assert json.loads(skill_view("support-case-files"))["error"] == "case_file_scope_required"
print("file_only=passed")
print("B1_VERIFY_OK")
