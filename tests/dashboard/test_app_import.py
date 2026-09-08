"""Direct-file launch must not depend on pytest's repository import path."""

import os
from pathlib import Path
import subprocess
import sys


def test_dashboard_import_without_pythonpath(tmp_path):
    app = Path(__file__).resolve().parents[2] / "src/dashboard/app.py"
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    # Isolated Python removes cwd/PYTHONPATH, reproducing the missing root
    # in a Streamlit direct-file launch. Avoid executing the UI's main().
    result = subprocess.run(
        [sys.executable, "-I", "-c",
         "import runpy, sys; runpy.run_path(sys.argv[1], run_name='launch_test')",
         str(app)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
