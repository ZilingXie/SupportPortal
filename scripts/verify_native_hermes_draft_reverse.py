"""Reverse checks in child processes; never rewrite the working tree."""
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
BASELINE = "4ecea0431f1b84490bab4bf51220faeef815e6b2"


def check(label, setup, test, expected):
    code = setup + "\nimport pytest\nraise SystemExit(pytest.main(['-xq', " + repr(test) + "]))\n"
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if result.returncode != 1 or expected not in result.stdout or "1 failed" not in result.stdout:
        print(result.stdout)
        raise SystemExit(f"{label}: mutation was not detected at the expected assertion")
    print(f"{label}: expected regression failure detected")


if __name__ == "__main__":
    check("C1 original name rules", f"""
import subprocess
import backend.services.hermes_customer_reply_basis as target
source = subprocess.check_output(['git', 'show', '{BASELINE}:backend/services/hermes_customer_reply_basis.py'], text=True)
exec(compile(source, target.__file__, 'exec'), target.__dict__)
""", "backend/tests/test_hermes_customer_reply_basis.py::test_technical_name_is_not_customer_name",
          "unauthorized_field_request")
    check("C3 candidate cap removed", """
from pathlib import Path
import backend.services.automation_ecs_store as target
source = Path(target.__file__).read_text()
assert source.count('if attempt >= 2:') == 1
exec(compile(source.replace('if attempt >= 2:', 'if False:'), target.__file__, 'exec'), target.__dict__)
""", "backend/tests/test_native_hermes_draft_save.py::test_two_rejections_third_submission_blocked_after_restart[memory]",
          "assert 200 == 422")
