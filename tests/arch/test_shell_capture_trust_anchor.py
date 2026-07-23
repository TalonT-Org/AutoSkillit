"""Architecture guards for descriptor-anchored shell capture."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC = _REPO_ROOT / "src" / "autoskillit"

_PROJECT_TEMP_CLEANUP_DEBT = {
    "core/io.py": {
        "owner": "core IO",
        "reason": "atomic temp writes still use mkdir/mkstemp/os.replace by pathname",
        "tracking_issue": "#4319",
    },
    "workspace/worktree.py": {
        "owner": "workspace worktree lifecycle",
        "reason": "worktree sidecars are still removed with pathname-based rmtree",
        "tracking_issue": "#4319",
    },
    "workspace/clone_registry.py": {
        "owner": "workspace clone registry",
        "reason": "registry-provided clone paths still reach removal callbacks",
        "tracking_issue": "#4319",
    },
    "workspace/clone.py": {
        "owner": "workspace clone lifecycle",
        "reason": "clone cleanup still uses pathname-based unlink/rmtree",
        "tracking_issue": "#4319",
    },
}


def _source(relative: str) -> str:
    return (_SRC / relative).read_text(encoding="utf-8")


def test_session_start_calls_canonical_capture_cleanup() -> None:
    source = _source("hooks/session_start_hook.py")
    assert "from _capture_artifacts import" in source
    assert "sweep_stale_captures(Path.cwd())" in source
    assert "shell_capture" not in source


def test_shell_capture_code_has_no_pathname_harness_or_cleanup() -> None:
    sources = {
        relative: _source(relative)
        for relative in (
            "hooks/shell_capture_hook.py",
            "hooks/_capture_artifacts.py",
        )
    }
    combined = "\n".join(sources.values())
    for forbidden in (
        "mkdir -p",
        '> "$__as_f"',
        ".unlink(",
        "os.unlink(",
        "shutil.rmtree(",
    ):
        assert forbidden not in combined, f"pathname capture operation reintroduced: {forbidden}"


def test_project_temp_cleanup_debt_is_narrow_and_owned() -> None:
    assert set(_PROJECT_TEMP_CLEANUP_DEBT) == {
        "core/io.py",
        "workspace/worktree.py",
        "workspace/clone_registry.py",
        "workspace/clone.py",
    }
    for relative, debt in _PROJECT_TEMP_CLEANUP_DEBT.items():
        assert (_SRC / relative).is_file()
        assert debt["owner"]
        assert debt["reason"]
        assert debt["tracking_issue"] == "#4319"
