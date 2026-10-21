"""Clean-directory smoke tests for the case-files overlay build chain.

R2-V2-B1-REPRO: the merge/verify scripts under deployment/hermes-case-files/v2-merge
used to hard-code one machine's paths, so the td54 build could not be
reproduced from the repository. These tests drive the scripts as subprocesses
from an empty working directory with every CF_*/SUPPORTPORTAL_ROOT variable
scrubbed, so any remaining machine-path dependency fails here.

Synthetic inputs are assembled from the scripts' own ``swap()`` anchors (read
via ``ast``), so they stay consistent with the merge logic without copying
live-image sources into the repository. The real-input rebuild is gated on
CF_B1_SRC/CF_B1_PROFILES (see deployment/hermes-case-files/v2-merge/README.md).
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
V2 = ROOT / "deployment" / "hermes-case-files" / "v2-merge"
SCRUBBED = ("CF_OVERLAY_WORK", "CF_OVERLAY_OUT", "CF_B1_SRC", "CF_B1_PROFILES",
            "CF_B1_OUT", "SUPPORTPORTAL_ROOT")


def _anchors(script: Path) -> dict[str, list[str]]:
    """Map the merge script's text variables (gw/st/pl/yml) to their anchors."""
    tree = ast.parse(script.read_text(encoding="utf-8"))
    found: dict[str, list[str]] = {}
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "swap" and isinstance(node.args[0], ast.Name)
                and isinstance(node.args[1], ast.Constant)):
            found.setdefault(node.args[0].id, []).append(node.args[1].value)
    assert found, f"no swap() anchors found in {script}"
    return found


def _run(script: Path, args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k not in SCRUBBED}
    return subprocess.run([sys.executable, str(script), *args], cwd=cwd, env=env,
                          capture_output=True, text=True, timeout=120)


class CleanDirectorySmokeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.cwd = self.tmp / "clean-cwd"
        self.cwd.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    # ---------- merge_b1_fix.py ----------
    def _synthetic_b1_src(self) -> tuple[Path, Path]:
        anchors = _anchors(V2 / "merge_b1_fix.py")
        src = self.tmp / "b1src"
        (src / "supportportal_agent_tools").mkdir(parents=True)
        (src / "skills_tool.py").write_text("\n".join(anchors["st"]), encoding="utf-8")
        (src / "api_server_runs.py").write_text("# gateway\n", encoding="utf-8")
        (src / "supportportal_skill_profile.py").write_text("# profile\n", encoding="utf-8")
        (src / "supportportal_agent_tools/__init__.py").write_text("# plugin\n", encoding="utf-8")
        (src / "supportportal_agent_tools/plugin.yaml").write_text("provides_tools: []\n", encoding="utf-8")
        profiles = self.tmp / "profiles"
        (profiles / "supportportal-investigation-v1/supportportal-investigation").mkdir(parents=True)
        (profiles / "supportportal-investigation-v1/supportportal-investigation/SKILL.md").write_text(
            "# synthetic\n", encoding="utf-8")
        return src, profiles

    def test_merge_b1_fix_requires_explicit_inputs(self):
        result = _run(V2 / "merge_b1_fix.py", [], self.cwd)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("required", result.stderr)
        for flag in ("--src", "--profiles", "--out"):
            self.assertIn(flag, result.stderr)
        self.assertFalse(list(self.cwd.iterdir()), "no output written into the cwd")

    def test_merge_b1_fix_rejects_missing_source_file(self):
        src, profiles = self._synthetic_b1_src()
        (src / "skills_tool.py").unlink()
        result = _run(V2 / "merge_b1_fix.py",
                      ["--src", str(src), "--profiles", str(profiles), "--sp", str(ROOT),
                       "--out", str(self.tmp / "out")], self.cwd)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing input file for --src", result.stderr)
        self.assertIn("skills_tool.py", result.stderr)

    def test_merge_b1_fix_builds_tree_from_explicit_inputs(self):
        src, profiles = self._synthetic_b1_src()
        out = self.tmp / "out-b1"
        result = _run(V2 / "merge_b1_fix.py",
                      ["--src", str(src), "--profiles", str(profiles), "--sp", str(ROOT),
                       "--out", str(out)], self.cwd)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("B1_MERGE_OK", result.stdout)
        produced = {str(p.relative_to(out)) for p in out.rglob("*") if p.is_file()}
        for rel in ("hermes-agent/tools/skills_tool.py",
                    "hermes-agent/gateway/platforms/api_server_runs.py",
                    "hermes-agent/supportportal_file_context.py",
                    "hermes-agent/supportportal_skill_profile.py",
                    "deploy-ecs/supportportal_agent_tools/__init__.py",
                    "deploy-ecs/supportportal_agent_tools/plugin.yaml",
                    "deploy-ecs/supportportal_agent_tools/case_files_bridge.py",
                    "deploy-ecs/case-files-skill/SKILL.md",
                    "investigation-profiles/supportportal-investigation-v1/supportportal-investigation/SKILL.md"):
            self.assertIn(rel, produced)
        merged = (out / "hermes-agent/tools/skills_tool.py").read_text(encoding="utf-8")
        self.assertIn("def _supportportal_listing_visible", merged)
        self.assertEqual(merged.count("_supportportal_listing_visible(s[\"name\"])"), 3)
        self.assertIn("file_skill_visible", merged)
        # The repository copies are the real bundle files, not placeholders.
        self.assertEqual(
            (out / "hermes-agent/supportportal_file_context.py").read_bytes(),
            (ROOT / "deployment/hermes-case-files/supportportal_file_context.py").read_bytes())
        self.assertFalse(list(self.cwd.iterdir()), "nothing leaked into the cwd")

    # ---------- merge_overlay_v2.py ----------
    def test_merge_overlay_v2_requires_work_dir(self):
        result = _run(V2 / "merge_overlay_v2.py", [], self.cwd)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("--work", result.stderr)

    def test_merge_overlay_v2_builds_tree_from_explicit_work_dir(self):
        anchors = _anchors(V2 / "merge_overlay_v2.py")
        work = self.tmp / "work"
        (work / "from-new-image").mkdir(parents=True)
        (work / "from-td46/supportportal_agent_tools").mkdir(parents=True)
        (work / "td46/investigation-profiles/p/s").mkdir(parents=True)
        (work / "from-new-image/api_server_runs.py").write_text(
            "\n".join(anchors["gw"]) + "\nfailure_reason = None\n", encoding="utf-8")
        (work / "from-td46/skills_tool.py").write_text("\n".join(anchors["st"]), encoding="utf-8")
        (work / "from-td46/supportportal_agent_tools/__init__.py").write_text(
            "\n".join(anchors["pl"]), encoding="utf-8")
        (work / "from-td46/supportportal_agent_tools/plugin.yaml").write_text(
            "\n".join(anchors["yml"]), encoding="utf-8")
        (work / "supportportal_skill_profile.py").write_text("# profile\n", encoding="utf-8")
        (work / "td46/investigation-profiles/p/s/SKILL.md").write_text("# s\n", encoding="utf-8")
        out = self.tmp / "out-v2"
        result = _run(V2 / "merge_overlay_v2.py",
                      ["--work", str(work), "--sp", str(ROOT), "--out", str(out)], self.cwd)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("MERGE_OK", result.stdout)
        gw = (out / "hermes-agent/gateway/platforms/api_server_runs.py").read_text(encoding="utf-8")
        self.assertEqual(gw.count("bind_profile(body.get"), 2)
        self.assertIn("bind_file_scope", gw)
        pl = (out / "deploy-ecs/supportportal_agent_tools/__init__.py").read_text(encoding="utf-8")
        self.assertIn('TOOLSETS["supportportal_files"]', pl)
        yml = (out / "deploy-ecs/supportportal_agent_tools/plugin.yaml").read_text(encoding="utf-8")
        self.assertEqual(yml.count("support_case_files_"), 5)
        self.assertTrue((out / "investigation-profiles/p/s/SKILL.md").is_file())
        self.assertFalse((work / "tree").exists(), "--out honored instead of <work>/tree")

    # ---------- verifiers ----------
    def test_verifiers_reject_missing_or_foreign_tree(self):
        for script in ("verify_b1.py", "verify_merged_overlay.py"):
            with self.subTest(script=script):
                no_args = _run(V2 / script, [], self.cwd)
                self.assertEqual(no_args.returncode, 2, no_args.stderr)
                self.assertIn("--tree", no_args.stderr)
                foreign = _run(V2 / script, ["--tree", str(self.tmp)], self.cwd)
                self.assertNotEqual(foreign.returncode, 0)
                self.assertIn("not a", foreign.stderr)


class RealInputRebuildTests(unittest.TestCase):
    """Rebuild from live-image inputs when the operator provides them.

    Set CF_B1_SRC and CF_B1_PROFILES (extraction steps in v2-merge/README.md);
    optionally CF_B1_EXPECTED_TREE to assert a byte-identical rebuild.
    """

    def test_rebuild_and_verify_b1_from_live_inputs(self):
        src = os.environ.get("CF_B1_SRC")
        profiles = os.environ.get("CF_B1_PROFILES")
        if not (src and profiles):
            self.skipTest("set CF_B1_SRC and CF_B1_PROFILES to rebuild from live-image inputs")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "tree-b1"
            cwd = Path(tmp) / "cwd"
            cwd.mkdir()
            merge = _run(V2 / "merge_b1_fix.py",
                         ["--src", src, "--profiles", profiles, "--sp", str(ROOT), "--out", str(out)], cwd)
            self.assertEqual(merge.returncode, 0, merge.stderr)
            self.assertIn("B1_MERGE_OK", merge.stdout)
            verify = _run(V2 / "verify_b1.py", ["--tree", str(out)], cwd)
            self.assertEqual(verify.returncode, 0, verify.stderr or verify.stdout)
            self.assertIn("B1_VERIFY_OK", verify.stdout)
            expected = os.environ.get("CF_B1_EXPECTED_TREE")
            if expected:
                diff = subprocess.run(
                    ["diff", "-r", "-x", "__pycache__", "-x", "Dockerfile.overlay-b1", expected, str(out)],
                    capture_output=True, text=True)
                self.assertEqual(diff.returncode, 0, diff.stdout + diff.stderr)


if __name__ == "__main__":
    unittest.main()
