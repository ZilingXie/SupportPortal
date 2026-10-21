"""R2-V2-B1 fix: combined profile + file-scope skill visibility (base 180e2c98).

Inputs (no machine paths are baked in; see README.md for extraction):
  --src DIR    (or CF_B1_SRC)  live-image extracted files: skills_tool.py,
               api_server_runs.py, supportportal_skill_profile.py,
               supportportal_agent_tools/{__init__.py,plugin.yaml}
  --profiles DIR (or CF_B1_PROFILES) investigation-profiles/ extracted from the
               live image
  --sp DIR     (or SUPPORTPORTAL_ROOT) SupportPortal checkout; defaults to the
               checkout this script lives in
  --out DIR    (or CF_B1_OUT)  output tree (no default; must not be the live tree)
"""
import argparse
import os
import shutil
import sys
from pathlib import Path


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    env = os.environ.get
    parser.add_argument("--src", default=env("CF_B1_SRC"), required=not env("CF_B1_SRC"),
                        help="live-image extracted source files (env CF_B1_SRC)")
    parser.add_argument("--profiles", default=env("CF_B1_PROFILES"), required=not env("CF_B1_PROFILES"),
                        help="investigation-profiles dir from the live image (env CF_B1_PROFILES)")
    parser.add_argument("--sp", default=env("SUPPORTPORTAL_ROOT") or str(Path(__file__).resolve().parents[3]),
                        help="SupportPortal checkout root (env SUPPORTPORTAL_ROOT; default: this checkout)")
    parser.add_argument("--out", default=env("CF_B1_OUT"), required=not env("CF_B1_OUT"),
                        help="output tree dir (env CF_B1_OUT)")
    return parser.parse_args(argv)


_args = _parse_args()
SP = Path(_args.sp).expanduser().resolve()
SRC = Path(_args.src).expanduser().resolve()
PROFILES = Path(_args.profiles).expanduser().resolve()
OUT = Path(_args.out).expanduser().resolve()
for required, label in ((SRC, "--src"), (PROFILES, "--profiles"),
                        (SP / "deployment/hermes-case-files", "--sp/deployment/hermes-case-files")):
    if not required.is_dir():
        sys.exit(f"missing input directory for {label}: {required}")
for name in ("skills_tool.py", "api_server_runs.py", "supportportal_skill_profile.py",
             "supportportal_agent_tools/__init__.py", "supportportal_agent_tools/plugin.yaml"):
    if not (SRC / name).is_file():
        sys.exit(f"missing input file for --src: {SRC / name}")

def swap(text, anchor, replacement, label):
    n = text.count(anchor)
    assert n == 1, f"{label}: anchor count {n} (expected 1)"
    return text.replace(anchor, replacement)

st = (SRC / "skills_tool.py").read_text()

# 1. skills_list profile branch: append the file skill entry when visible.
st = swap(st,
    '''    from supportportal_skill_profile import active_profile, profile_list
    if active_profile():
        return profile_list(category)
''',
    '''    from supportportal_skill_profile import active_profile, profile_entries
    if active_profile():
        # R2-V2-B1: a run may hold BOTH the Investigation skill profile and a
        # work-phase file scope. The profile still narrows its own entries,
        # and the case-file skill is additionally listed when the file scope
        # makes it visible.
        from supportportal_file_context import file_skill_visible
        entries = profile_entries() if category in {None, "investigation"} else []
        if file_skill_visible("support-case-files"):
            for candidate in _find_all_skills():
                if str(candidate.get("name") or "") == "support-case-files":
                    entries.append(dict(candidate))
                    break
        return json.dumps({"success": True, "skills": entries, "count": len(entries), "categories": ["investigation"]})
''', "skills_list combined profile branch")

# 2. skill_view: profile shortcut exempts the file skill; visibility gate too.
st = swap(st,
    '''        from supportportal_skill_profile import active_profile, profile_view, skill_visible
        if active_profile():
            return profile_view(name, file_path)
        if not skill_visible(name):
            return json.dumps({"success": False, "error": "skill_profile_denied"})
        from supportportal_file_context import skill_allowed
''',
    '''        from supportportal_skill_profile import active_profile, profile_view, skill_visible
        from supportportal_file_context import file_skill_visible
        if active_profile() and not file_skill_visible(name):
            return profile_view(name, file_path)
        if not skill_visible(name) and not file_skill_visible(name):
            return json.dumps({"success": False, "error": "skill_profile_denied"})
        from supportportal_file_context import skill_allowed
''', "skill_view combined gates")

# 3. _skill_view_with_bump wrapper: mirror the same exemption.
st = swap(st,
    '''    from supportportal_skill_profile import active_profile, profile_view, skill_visible
    if active_profile():
        return profile_view(name, args.get("file_path"))
    if not skill_visible(name):
        return json.dumps({"success": False, "error": "skill_profile_denied"})
''',
    '''    from supportportal_skill_profile import active_profile, profile_view, skill_visible
    from supportportal_file_context import file_skill_visible
    if active_profile() and not file_skill_visible(name):
        return profile_view(name, args.get("file_path"))
    if not skill_visible(name) and not file_skill_visible(name):
        return json.dumps({"success": False, "error": "skill_profile_denied"})
''', "skill_view wrapper combined gates")

# 4. Shared listing predicate: profile names OR visible file skill, both gated by skill_allowed.
st = swap(st,
    '''def _find_all_skills(*, skip_disabled: bool = False) -> List[Dict[str, Any]]:
''',
    '''def _supportportal_listing_visible(name) -> bool:
    """Combined Investigation-profile + case-file visibility for skill listings (R2-V2-B1)."""
    from supportportal_file_context import file_skill_visible, skill_allowed
    from supportportal_skill_profile import skill_visible
    return (skill_visible(name) or file_skill_visible(name)) and skill_allowed(name)


def _find_all_skills(*, skip_disabled: bool = False) -> List[Dict[str, Any]]:
''', "listing predicate helper")

# 5-7. The three listing filters use the shared predicate.
st = swap(st,
    '''        from supportportal_file_context import skill_allowed
        from supportportal_skill_profile import skill_visible
        return [dict(s) for s in cached[2] if skill_visible(s["name"]) and skill_allowed(s["name"])]
''',
    '''        return [dict(s) for s in cached[2] if _supportportal_listing_visible(s["name"])]
''', "cached listing filter")
st = swap(st,
    '''    from supportportal_file_context import skill_allowed
    from supportportal_skill_profile import skill_visible
    return [dict(s) for s in skills if skill_visible(s["name"]) and skill_allowed(s["name"])]
''',
    '''    return [dict(s) for s in skills if _supportportal_listing_visible(s["name"])]
''', "plain listing filter")
st = swap(st,
    '''        from supportportal_file_context import skill_allowed
        from supportportal_skill_profile import skill_visible
        all_skills = [s for s in all_skills if skill_visible(s["name"]) and skill_allowed(s["name"])]
''',
    '''        all_skills = [s for s in all_skills if _supportportal_listing_visible(s["name"])]
''', "all_skills listing filter")

(OUT / "hermes-agent/tools").mkdir(parents=True, exist_ok=True)
(OUT / "hermes-agent/tools/skills_tool.py").write_text(st)

# Remaining layers: gateway/plugin/skill_profile byte-identical from the live image;
# file_context carries the new helper; bridge + skill from the SP bundle.
(OUT / "hermes-agent/gateway/platforms").mkdir(parents=True, exist_ok=True)
shutil.copyfile(SRC / "api_server_runs.py", OUT / "hermes-agent/gateway/platforms/api_server_runs.py")
shutil.copyfile(SP / "deployment/hermes-case-files/supportportal_file_context.py",
                OUT / "hermes-agent/supportportal_file_context.py")
shutil.copyfile(SRC / "supportportal_skill_profile.py", OUT / "hermes-agent/supportportal_skill_profile.py")
(OUT / "deploy-ecs/supportportal_agent_tools").mkdir(parents=True, exist_ok=True)
shutil.copyfile(SRC / "supportportal_agent_tools/__init__.py", OUT / "deploy-ecs/supportportal_agent_tools/__init__.py")
shutil.copyfile(SRC / "supportportal_agent_tools/plugin.yaml", OUT / "deploy-ecs/supportportal_agent_tools/plugin.yaml")
shutil.copyfile(SP / "deployment/hermes-case-files/case_files_bridge.py",
                OUT / "deploy-ecs/supportportal_agent_tools/case_files_bridge.py")
shutil.copytree(SP / "deployment/hermes-case-files/skills/support-case-files",
                OUT / "deploy-ecs/case-files-skill", dirs_exist_ok=True)
shutil.copytree(PROFILES, OUT / "investigation-profiles", dirs_exist_ok=True)

print("B1_MERGE_OK")
for p in sorted(OUT.rglob("*")):
    if p.is_file():
        print(" ", p.relative_to(OUT), p.stat().st_size)
