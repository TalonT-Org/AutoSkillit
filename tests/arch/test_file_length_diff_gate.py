"""REQ-CNST-010 diff-scoped gate for changed source files.

Diff scope reuses tests._test_filter.git_changed_files against the base ref
resolved once at session configure time. Local runs with no resolved base ref
skip instead of falling back to the pre-existing full-tree violations; a
pull_request or merge_group event with no base ref fails instead of skipping.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from tests._test_filter import git_changed_files, resolve_test_base_ref_from_env
from tests.arch._policy_gate_plumbing import BaseRefContext, require_base_ref_or_skip

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


def _changed_files_or_skip(ctx: BaseRefContext) -> set[str]:
    """Return changed files, skipping only when skipping is permitted."""
    base_ref = require_base_ref_or_skip(ctx)
    changed = git_changed_files(REPO_ROOT, base_ref=base_ref)
    assert changed is not None, f"could not compute changed files against base ref {base_ref!r}"
    return changed


def test_no_diff_exceeds_line_limit(resolved_test_base: BaseRefContext) -> None:
    """REQ-CNST-010: changed src files must satisfy the diff-scoped cap."""
    changed = _changed_files_or_skip(resolved_test_base)
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


def test_resolve_base_ref_prefers_explicit_argument(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTOSKILLIT_TEST_BASE_REF", "env-base")
    monkeypatch.setenv("GITHUB_BASE_REF", "github-base")
    assert resolve_test_base_ref_from_env("cli-base") == "cli-base"


def test_resolve_base_ref_prefers_autoskillit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTOSKILLIT_TEST_BASE_REF", "explicit-base")
    monkeypatch.setenv("GITHUB_BASE_REF", "github-base")
    assert resolve_test_base_ref_from_env() == "explicit-base"


def test_resolve_base_ref_falls_through_empty_to_github(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUTOSKILLIT_TEST_BASE_REF", "")
    monkeypatch.setenv("GITHUB_BASE_REF", "github-base")
    assert resolve_test_base_ref_from_env() == "origin/github-base"


def test_resolve_base_ref_returns_none_without_refs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUTOSKILLIT_TEST_BASE_REF", raising=False)
    monkeypatch.delenv("GITHUB_BASE_REF", raising=False)
    assert resolve_test_base_ref_from_env() is None


def test_gate_fails_instead_of_skipping_on_pull_request_event() -> None:
    with pytest.raises(pytest.fail.Exception, match="pull_request/merge_group"):
        require_base_ref_or_skip(BaseRefContext(base_ref=None, gate_required=True))
    with pytest.raises(pytest.skip.Exception, match="no base ref resolved"):
        require_base_ref_or_skip(BaseRefContext(base_ref=None, gate_required=False))


@pytest.mark.parametrize(
    ("event_name", "expected"),
    [
        ("pull_request", True),
        ("merge_group", True),
        ("push", False),
        ("schedule", False),
    ],
)
def test_gate_required_follows_github_event_name(
    monkeypatch: pytest.MonkeyPatch,
    event_name: str,
    expected: bool,
) -> None:
    monkeypatch.setenv("GITHUB_EVENT_NAME", event_name)
    assert BaseRefContext.from_env(None).gate_required is expected


def test_gate_not_required_without_event_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_EVENT_NAME", raising=False)
    assert BaseRefContext.from_env(None).gate_required is False


def test_changed_files_failure_is_not_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(f"{__name__}.git_changed_files", lambda *_args, **_kwargs: None)
    with pytest.raises(AssertionError, match="could not compute changed files"):
        _changed_files_or_skip(BaseRefContext("explicit-base", False))
