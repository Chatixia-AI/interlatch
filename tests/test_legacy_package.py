"""The chronicle package, the name before the rename to Interlatch, only forwards what installs from then still run."""

import subprocess
import sys
from pathlib import Path

import interlatch
from interlatch import install, windows

LEGACY = Path(__file__).resolve().parent.parent / "src" / "chronicle"


def test_legacy_package_holds_no_implementation():
    assert sorted(p.name for p in LEGACY.glob("*.py")) == ["__init__.py", "__main__.py", "windows.py"]
    assert not [p for p in LEGACY.iterdir() if p.is_dir() and p.name != "__pycache__"]


def test_python_m_chronicle_runs_interlatch():
    out = subprocess.run([sys.executable, "-m", "chronicle", "--version"], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert interlatch.__version__ in out.stdout


def test_windows_runner_and_restart_code_forward():
    from chronicle.windows import RESTART_EXIT, run_hidden

    assert RESTART_EXIT == windows.RESTART_EXIT and run_hidden is windows.run_hidden
    out = subprocess.run([sys.executable, "-m", "chronicle.windows", "--help"], capture_output=True, text=True,
                         timeout=60)
    assert out.returncode == 0, out.stderr
    assert "--log" in out.stdout


def test_task_python_reads_either_module_name(tmp_path):
    python = tmp_path / "python"
    for name in ("interlatch", "chronicle"):
        assert install._task_python(f"{python} -m {name}") == python
    assert install._task_python(f"{python} -m other") == Path(sys.executable)
