"""Three-way merge for the case-files Hermes overlay v2.

Bases:
  gateway api_server_runs.py : new live image 2cdc5a8a (keeps p2-188 failure_reason fix)
  skills_tool.py             : td46 image 6198ca25 (keeps full p2-203 profile stack)
  plugin __init__.py/.yaml   : td46 image 6198ca25 (keeps support_zac_consult)

Adds:
  p2-203 gateway hunks (validate_profile block + bind_profile at both run sites)
  R2 file hunks (bind_file_scope at the approval site, skill_allowed filters,
  skill_view gate, plugin TOOLSETS/bridge/draft-scope, yaml provides_tools)

Every replacement asserts exactly one anchor match.

Inputs (no machine paths are baked in; see README.md for extraction):
  --work DIR   (or CF_OVERLAY_WORK)  work dir holding from-new-image/,
               from-td46/, supportportal_skill_profile.py, td46/investigation-profiles/
  --sp DIR     (or SUPPORTPORTAL_ROOT) SupportPortal checkout; defaults to the
               checkout this script lives in
  --out DIR    (or CF_OVERLAY_OUT)   output tree; defaults to <work>/tree
"""
import argparse
import os
import shutil
import sys
from pathlib import Path


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--work", default=os.environ.get("CF_OVERLAY_WORK"),
                        required=not os.environ.get("CF_OVERLAY_WORK"),
                        help="work dir with from-new-image/, from-td46/, td46/ (env CF_OVERLAY_WORK)")
    parser.add_argument("--sp", default=os.environ.get("SUPPORTPORTAL_ROOT")
                        or str(Path(__file__).resolve().parents[3]),
                        help="SupportPortal checkout root (env SUPPORTPORTAL_ROOT; default: this checkout)")
    parser.add_argument("--out", default=os.environ.get("CF_OVERLAY_OUT"),
                        help="output tree dir (env CF_OVERLAY_OUT; default <work>/tree)")
    return parser.parse_args(argv)


_args = _parse_args()
W = Path(_args.work).expanduser().resolve()
SP = Path(_args.sp).expanduser().resolve()
NEW = W / "from-new-image"      # populated by caller
TD46 = W / "from-td46"          # populated by caller
OUT = Path(_args.out).expanduser().resolve() if _args.out else W / "tree"
for required, label in ((NEW, "--work/from-new-image"), (TD46, "--work/from-td46"),
                        (W / "td46/investigation-profiles", "--work/td46/investigation-profiles"),
                        (SP / "deployment/hermes-case-files", "--sp/deployment/hermes-case-files")):
    if not required.is_dir():
        sys.exit(f"missing input directory for {label}: {required}")

def swap(text, anchor, replacement, label):
    n = text.count(anchor)
    assert n == 1, f"{label}: anchor count {n} (expected 1)"
    return text.replace(anchor, replacement)

# ---------- 1. gateway ----------
gw = (NEW / "api_server_runs.py").read_text()

gw = swap(gw,
    '''    try:
        body = await request.json()
    except Exception:
        return web.json_response(_openai_error("Invalid JSON"), status=400)

    body, room_error = await self._normalize_room_dispatch(request, body)
''',
    '''    try:
        body = await request.json()
    except Exception:
        return web.json_response(_openai_error("Invalid JSON"), status=400)

    from supportportal_skill_profile import validate_profile, bind_profile
    try:
        validate_profile(body.get("supportportal_skill_profile") if isinstance(body, dict) else None)
    except ValueError as exc:
        return web.json_response(_openai_error(str(exc), code="skill_profile_invalid"), status=422)

    body, room_error = await self._normalize_room_dispatch(request, body)
''', "gateway validate_profile block")

gw = swap(gw,
    '''            with self._profile_scope(request_profile):
                agent = self._create_agent(
''',
    '''            with self._profile_scope(request_profile), bind_profile(body.get("supportportal_skill_profile"), run_id):
                agent = self._create_agent(
''', "gateway bind_profile site1")

gw = swap(gw,
    '''                room_policy_token = None
                with self._profile_scope(request_profile):
                    try:
''',
    '''                room_policy_token = None
                from supportportal_file_context import bind_file_scope
                with self._profile_scope(request_profile), bind_profile(body.get("supportportal_skill_profile"), run_id), bind_file_scope(body.get("supportportal_file_scope"), session_id, workspace_dir):
                    try:
''', "gateway bind_profile+bind_file_scope site2")

(OUT / "hermes-agent/gateway/platforms").mkdir(parents=True, exist_ok=True)
(OUT / "hermes-agent/gateway/platforms/api_server_runs.py").write_text(gw)

# ---------- 2. skills_tool ----------
st = (TD46 / "skills_tool.py").read_text()

st = swap(st,
    '''        from supportportal_skill_profile import skill_visible
        return [dict(s) for s in cached[2] if skill_visible(s["name"])]
''',
    '''        from supportportal_file_context import skill_allowed
        from supportportal_skill_profile import skill_visible
        return [dict(s) for s in cached[2] if skill_visible(s["name"]) and skill_allowed(s["name"])]
''', "skills_tool cached return")

st = swap(st,
    '''    _SKILLS_CACHE[cache_key] = (signature, now, skills)
    from supportportal_skill_profile import skill_visible
    return [dict(s) for s in skills if skill_visible(s["name"])]
''',
    '''    _SKILLS_CACHE[cache_key] = (signature, now, skills)
    from supportportal_file_context import skill_allowed
    from supportportal_skill_profile import skill_visible
    return [dict(s) for s in skills if skill_visible(s["name"]) and skill_allowed(s["name"])]
''', "skills_tool plain return")

st = swap(st,
    '''        from supportportal_skill_profile import skill_visible
        all_skills = [s for s in all_skills if skill_visible(s["name"])]
''',
    '''        from supportportal_file_context import skill_allowed
        from supportportal_skill_profile import skill_visible
        all_skills = [s for s in all_skills if skill_visible(s["name"]) and skill_allowed(s["name"])]
''', "skills_tool all_skills filter")

st = swap(st,
    '''        from supportportal_skill_profile import active_profile, profile_view, skill_visible
        if active_profile():
            return profile_view(name, file_path)
        if not skill_visible(name):
            return json.dumps({"success": False, "error": "skill_profile_denied"})
        lookup_error = _skill_lookup_path_error(name)
''',
    '''        from supportportal_skill_profile import active_profile, profile_view, skill_visible
        if active_profile():
            return profile_view(name, file_path)
        if not skill_visible(name):
            return json.dumps({"success": False, "error": "skill_profile_denied"})
        from supportportal_file_context import skill_allowed
        if not skill_allowed(name):
            return json.dumps({"success": False, "error": "case_file_scope_required"})
        lookup_error = _skill_lookup_path_error(name)
''', "skills_tool skill_view gate")

(OUT / "hermes-agent/tools").mkdir(parents=True, exist_ok=True)
(OUT / "hermes-agent/tools/skills_tool.py").write_text(st)

# ---------- 3. plugin __init__.py ----------
pl = (TD46 / "supportportal_agent_tools/__init__.py").read_text()

pl = swap(pl,
    '''                "basis": {"type": "object", "description": "What the reply is based on (facts, tool results)."},
''',
    '''                "basis": {"type": "object", "description": "What the reply is based on (facts, tool results)."},
                "selected_file_ids": {"type": "array", "items": {"type": "integer"}, "description": "Explicit Map IDs for the approved Investigation reply, [] for none."},
''', "plugin draft schema selected_file_ids")

pl = swap(pl,
    '''    "supportportal_persona": ["support_get_case_context", "support_save_reply_draft", "support_escalate_human"],
}
''',
    '''    "supportportal_persona": ["support_get_case_context", "support_save_reply_draft", "support_escalate_human"],
}

TOOLSETS["supportportal_files"] = ["support_case_files_" + action for action in ("list", "download", "read", "search", "publish")]
''', "plugin TOOLSETS supportportal_files")

pl = swap(pl,
    '''        body = dict(args)
        if tool_name == "zac_consult":
''',
    '''        body = dict(args)
        if tool_name == "save_reply_draft" and args.get("selected_file_ids"):
            from supportportal_file_context import current_scope
            scope = current_scope()
            if not scope or scope.get("phase") != "persona" or turn_id != scope["turn_id"]:
                return json.dumps({"ok": False, "code": "file_scope_invalid"})
            body["file_scope"] = scope["capability"]
        if tool_name == "zac_consult":
''', "plugin save_reply_draft file scope")

pl = swap(pl,
    '''def register(ctx) -> None:
''',
    '''def register(ctx) -> None:
    from .case_files_bridge import register as register_files
    register_files(ctx)
''', "plugin register_files")

(OUT / "deploy-ecs/supportportal_agent_tools").mkdir(parents=True, exist_ok=True)
(OUT / "deploy-ecs/supportportal_agent_tools/__init__.py").write_text(pl)

# ---------- 4. plugin.yaml ----------
yml = (TD46 / "supportportal_agent_tools/plugin.yaml").read_text()
yml = swap(yml,
    '''  - support_escalate_human
''',
    '''  - support_escalate_human
  - support_case_files_list
  - support_case_files_download
  - support_case_files_read
  - support_case_files_search
  - support_case_files_publish
''', "plugin.yaml provides_tools")
(OUT / "deploy-ecs/supportportal_agent_tools/plugin.yaml").write_text(yml)

# ---------- 5. bundle + td46 aux files ----------
shutil.copyfile(SP / "deployment/hermes-case-files/supportportal_file_context.py",
                OUT / "hermes-agent/supportportal_file_context.py")
shutil.copyfile(W / "supportportal_skill_profile.py",
                OUT / "hermes-agent/supportportal_skill_profile.py")
shutil.copyfile(SP / "deployment/hermes-case-files/case_files_bridge.py",
                OUT / "deploy-ecs/supportportal_agent_tools/case_files_bridge.py")
shutil.copytree(SP / "deployment/hermes-case-files/skills/support-case-files",
                OUT / "deploy-ecs/case-files-skill", dirs_exist_ok=True)
shutil.copytree(W / "td46/investigation-profiles",
                OUT / "investigation-profiles", dirs_exist_ok=True)

print("MERGE_OK")
for p in sorted(OUT.rglob("*")):
    if p.is_file():
        print(" ", p.relative_to(OUT), p.stat().st_size)
