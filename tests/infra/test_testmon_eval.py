from __future__ import annotations

import ast
import importlib
import importlib.util
from pathlib import Path

import pytest

pytestmark = [pytest.mark.small]

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_testmon_importable():
    """pytest-testmon must be importable when installed with dev extras."""
    mod = importlib.import_module("testmon")
    assert hasattr(mod, "TESTMON_VERSION")


def test_testmondata_gitignored():
    """The .testmondata SQLite DB must be gitignored."""
    gitignore = (REPO_ROOT / ".gitignore").read_text()
    assert ".testmondata" in gitignore


def test_testmon_not_active_by_default(pytestconfig: pytest.Config):
    """testmon must not be active unless explicitly opted in via --testmon."""
    plugin = pytestconfig.pluginmanager.get_plugin("pytest-testmon")
    if plugin is None:
        pytest.skip("testmon plugin not registered")
    assert "--testmon" not in pytestconfig.getini("addopts")


def test_benchmark_script_exists():
    """The testmon benchmark script must exist and be valid Python."""
    script = REPO_ROOT / "scripts" / "benchmark-testmon.py"
    assert script.exists(), f"Missing: {script}"
    ast.parse(script.read_text())


def test_benchmark_compute_metrics():
    """compute_selection_metrics returns correct precision/recall/f1."""
    script = REPO_ROOT / "scripts" / "benchmark-testmon.py"
    spec = importlib.util.spec_from_file_location("benchmark_testmon", script)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    testmon_set = {"test_a.py", "test_b.py", "test_c.py"}
    cascade_set = {"test_b.py", "test_c.py", "test_d.py", "test_e.py"}
    metrics = mod.compute_selection_metrics(testmon_set, cascade_set)
    assert metrics["testmon_only"] == {"test_a.py"}
    assert metrics["cascade_only"] == {"test_d.py", "test_e.py"}
    assert metrics["overlap"] == {"test_b.py", "test_c.py"}
    assert metrics["testmon_count"] == 3
    assert metrics["cascade_count"] == 4
    assert metrics["overlap_count"] == 2
    assert metrics["jaccard_similarity"] == 2 / 5


def test_taskfile_has_testmon_tasks():
    """Taskfile must define testmon evaluation tasks."""
    taskfile = (REPO_ROOT / "Taskfile.yml").read_text()
    for task_name in ("testmon-build", "testmon-run", "testmon-benchmark"):
        assert f"  {task_name}:" in taskfile, f"Missing task: {task_name}"
