"""REQ-CNST-010 diff-scoped gate for changed source files.

Diff scope reuses tests._test_filter.git_changed_files. Local runs with no
resolved base ref skip instead of falling back to the pre-existing full-tree
violations. Base-ref resolution is kept here because CI can set
AUTOSKILLIT_TEST_BASE_REF to an empty string; that value must fall through to
GITHUB_BASE_REF before calling the shared helper.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

from tests._test_filter import git_changed_files

pytestmark = [pytest.mark.layer("arch"), pytest.mark.medium]

REPO_ROOT = Path(__file__).resolve().parents[2]
_CHECK_SCRIPT = REPO_ROOT / "scripts" / "check_file_lengths.py"


def _load_check_module():
    spec = importlib.util.spec_from_file_location("check_file_lengths", _CHECK_SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _resolve_base_ref() -> str | None:
    """Resolve the explicit test base, treating an empty value as unset."""
    return os.environ.get("AUTOSKILLIT_TEST_BASE_REF") or os.environ.get("GITHUB_BASE_REF") or None


def _changed_files_or_skip() -> set[str]:
    """Return changed files, skipping only when no base ref is available."""
    base_ref = _resolve_base_ref()
    if base_ref is None:
        pytest.skip("no base ref resolved (AUTOSKILLIT_TEST_BASE_REF/GITHUB_BASE_REF unset)")
    changed = git_changed_files(REPO_ROOT, base_ref=base_ref)
    assert changed is not None, f"could not compute changed files against base ref {base_ref!r}"
    return changed


def test_no_diff_exceeds_line_limit() -> None:
    """REQ-CNST-010: changed src files must satisfy the diff-scoped cap."""
    changed = _changed_files_or_skip()
    check = _load_check_module()
    violations = []
    for rel in sorted(changed):
        if not rel.startswith("src/autoskillit/") or not rel.endswith(".py"):
            continue
        path = REPO_ROOT / rel
        if not path.is_file():
            continue
        message = check.check_file(path)
        if message:
            violations.append(message)
    assert not violations, "Diff-scoped file-length violations (REQ-CNST-010):\n" + "\n".join(
        f"  {violation}" for violation in violations
    )


def test_resolve_base_ref_prefers_autoskillit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTOSKILLIT_TEST_BASE_REF", "explicit-base")
    monkeypatch.setenv("GITHUB_BASE_REF", "github-base")
    assert _resolve_base_ref() == "explicit-base"


def test_resolve_base_ref_falls_through_empty_to_github(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUTOSKILLIT_TEST_BASE_REF", "")
    monkeypatch.setenv("GITHUB_BASE_REF", "github-base")
    assert _resolve_base_ref() == "github-base"


def test_resolve_base_ref_returns_none_without_refs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUTOSKILLIT_TEST_BASE_REF", raising=False)
    monkeypatch.delenv("GITHUB_BASE_REF", raising=False)
    assert _resolve_base_ref() is None


def test_changed_files_failure_is_not_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTOSKILLIT_TEST_BASE_REF", "explicit-base")
    monkeypatch.setattr(f"{__name__}.git_changed_files", lambda *_args, **_kwargs: None)
    with pytest.raises(AssertionError, match="could not compute changed files"):
        _changed_files_or_skip()
