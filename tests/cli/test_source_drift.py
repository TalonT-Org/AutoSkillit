"""Tests for cli/_source_drift.py — source-drift boot gate."""

from __future__ import annotations

import ast
import io
import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_install_info(
    install_type=None,
    commit_id: str | None = "abc1234abcd",
    requested_revision: str | None = "integration",
    url: str | None = "https://github.com/TalonT-Org/AutoSkillit.git",
    editable_source: Path | None = None,
) -> Any:
    from autoskillit.cli._source_drift import InstallInfo, InstallType

    return InstallInfo(
        install_type=install_type or InstallType.GIT_VCS,
        commit_id=commit_id,
        requested_revision=requested_revision,
        url=url,
        editable_source=editable_source,
    )


def _fake_dist(
    *,
    vcs: str | None = None,
    commit_id: str | None = None,
    requested_revision: str | None = None,
    vcs_url: str | None = None,
    editable: bool = False,
    file_url: str | None = None,
    no_direct_url: bool = False,
) -> Any:
    import importlib.metadata

    fake = MagicMock(spec=importlib.metadata.Distribution)
    if no_direct_url:
        fake.read_text.return_value = None
        return fake

    data: dict[str, Any] = {}
    if vcs == "git":
        data["url"] = vcs_url or "https://github.com/TalonT-Org/AutoSkillit.git"
        vcs_info: dict[str, Any] = {"vcs": "git"}
        if commit_id:
            vcs_info["commit_id"] = commit_id
        if requested_revision:
            vcs_info["requested_revision"] = requested_revision
        data["vcs_info"] = vcs_info
    elif editable and file_url:
        data["url"] = file_url
        data["dir_info"] = {"editable": True}
    elif file_url:
        data["url"] = file_url
        data["dir_info"] = {"editable": False}

    fake.read_text.return_value = json.dumps(data)
    return fake


# ---------------------------------------------------------------------------
# detect_install
# ---------------------------------------------------------------------------


def test_classify_git_vcs_stable(monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib.metadata

    from autoskillit.cli._source_drift import InstallType, detect_install

    dist = _fake_dist(
        vcs="git",
        commit_id="abcdef1234567890",
        requested_revision="stable",
        vcs_url="https://github.com/TalonT-Org/AutoSkillit.git",
    )
    monkeypatch.setattr(importlib.metadata.Distribution, "from_name", staticmethod(lambda _: dist))

    info = detect_install()
    assert info.install_type == InstallType.GIT_VCS
    assert info.commit_id == "abcdef1234567890"
    assert info.requested_revision == "stable"
    assert info.url == "https://github.com/TalonT-Org/AutoSkillit.git"
    assert info.editable_source is None


def test_classify_git_vcs_integration(monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib.metadata

    from autoskillit.cli._source_drift import InstallType, detect_install

    dist = _fake_dist(
        vcs="git",
        commit_id="fed9876543210abc",
        requested_revision="integration",
    )
    monkeypatch.setattr(importlib.metadata.Distribution, "from_name", staticmethod(lambda _: dist))

    info = detect_install()
    assert info.install_type == InstallType.GIT_VCS
    assert info.requested_revision == "integration"


def test_classify_local_editable(monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib.metadata

    from autoskillit.cli._source_drift import InstallType, detect_install

    dist = _fake_dist(editable=True, file_url="file:///home/x/repo")
    monkeypatch.setattr(importlib.metadata.Distribution, "from_name", staticmethod(lambda _: dist))

    info = detect_install()
    assert info.install_type == InstallType.LOCAL_EDITABLE
    assert info.editable_source == Path("/home/x/repo")
    assert info.commit_id is None
    assert info.requested_revision is None


def test_classify_unknown_when_no_direct_url(monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib.metadata

    from autoskillit.cli._source_drift import InstallType, detect_install

    dist = _fake_dist(no_direct_url=True)
    monkeypatch.setattr(importlib.metadata.Distribution, "from_name", staticmethod(lambda _: dist))

    info = detect_install()
    assert info.install_type == InstallType.UNKNOWN
    assert info.commit_id is None


@pytest.mark.parametrize(
    "cwd_suffix",
    [".", "subdir1", "subdir2/deeper"],
)
def test_classify_deterministic_independent_of_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cwd_suffix: str
) -> None:
    """detect_install() returns the same result regardless of CWD."""
    import importlib.metadata

    from autoskillit.cli._source_drift import InstallType, detect_install

    cwd = tmp_path / cwd_suffix
    cwd.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(cwd)

    dist = _fake_dist(vcs="git", commit_id="aabbccdd", requested_revision="stable")
    monkeypatch.setattr(importlib.metadata.Distribution, "from_name", staticmethod(lambda _: dist))

    info = detect_install()
    assert info.install_type == InstallType.GIT_VCS
    assert info.commit_id == "aabbccdd"


# ---------------------------------------------------------------------------
# find_source_repo (tested via resolve_reference_sha interactions)
# ---------------------------------------------------------------------------


def test_resolve_ref_sha_prefers_autoskillit_source_repo_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AUTOSKILLIT_SOURCE_REPO env var takes precedence over CWD walk."""
    from autoskillit.cli._source_drift import find_source_repo

    repo = tmp_path / "myrepo"
    repo.mkdir()
    (repo / "src" / "autoskillit").mkdir(parents=True)

    monkeypatch.setenv("AUTOSKILLIT_SOURCE_REPO", str(repo))
    result = find_source_repo()
    assert result == repo


def test_resolve_ref_sha_source_repo_env_missing_path_falls_through(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When AUTOSKILLIT_SOURCE_REPO points to a non-existent path, CWD walk is used."""
    from autoskillit.cli._source_drift import find_source_repo

    # Also create an autoskillit repo under CWD so the walk can find it
    autoskillit_root = tmp_path / "autoskillit_root"
    autoskillit_root.mkdir()
    (autoskillit_root / "src" / "autoskillit").mkdir(parents=True)
    pyproject = autoskillit_root / "pyproject.toml"
    pyproject.write_bytes(b'[project]\nname = "autoskillit"\n')

    monkeypatch.setenv("AUTOSKILLIT_SOURCE_REPO", str(tmp_path / "does-not-exist"))
    monkeypatch.chdir(autoskillit_root)

    result = find_source_repo()
    assert result == autoskillit_root


def test_resolve_ref_sha_walks_cwd_for_source_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CWD walk locates a parent directory containing autoskillit source."""
    from autoskillit.cli._source_drift import find_source_repo

    monkeypatch.delenv("AUTOSKILLIT_SOURCE_REPO", raising=False)

    repo_root = tmp_path / "autoskillit"
    (repo_root / "src" / "autoskillit").mkdir(parents=True)
    pyproject = repo_root / "pyproject.toml"
    pyproject.write_bytes(b'[project]\nname = "autoskillit"\n')

    # CWD is a subdirectory of the repo root
    sub = repo_root / "tests" / "cli"
    sub.mkdir(parents=True)
    monkeypatch.chdir(sub)

    result = find_source_repo()
    assert result == repo_root


# ---------------------------------------------------------------------------
# resolve_reference_sha — git ls-remote behaviour
# ---------------------------------------------------------------------------


def test_resolve_ref_sha_uses_git_ls_remote_origin_refs_heads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Must call git -C <src> ls-remote origin refs/heads/integration."""
    from autoskillit.cli._source_drift import resolve_reference_sha

    info = _make_install_info(commit_id="aaa111", requested_revision="integration")

    repo = tmp_path / "src_repo"
    (repo / "src" / "autoskillit").mkdir(parents=True)
    monkeypatch.setenv("AUTOSKILLIT_SOURCE_REPO", str(repo))

    captured: list[list[str]] = []

    def spy_run(cmd: list[str], **kwargs: Any) -> Any:
        captured.append(list(cmd))
        result = MagicMock()
        result.stdout = "def456def456def456\trefs/heads/integration\n"
        result.returncode = 0
        return result

    monkeypatch.setattr(subprocess, "run", spy_run)

    sha = resolve_reference_sha(info, tmp_path, network=False)

    assert sha == "def456def456def456"
    assert any("ls-remote" in " ".join(cmd) for cmd in captured), (
        f"Expected git ls-remote call, got: {captured}"
    )
    heads_call = next((cmd for cmd in captured if "refs/heads/integration" in " ".join(cmd)), None)
    assert heads_call is not None, f"Expected refs/heads/integration call, got: {captured}"
    assert "rev-parse" not in " ".join(heads_call), "Must use ls-remote, not rev-parse"


def test_resolve_ref_sha_parses_sha_from_ls_remote_first_column(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SHA is parsed from the first whitespace-separated column of ls-remote output."""
    from autoskillit.cli._source_drift import resolve_reference_sha

    info = _make_install_info(commit_id="old111", requested_revision="integration")

    repo = tmp_path / "src_repo"
    (repo / "src" / "autoskillit").mkdir(parents=True)
    monkeypatch.setenv("AUTOSKILLIT_SOURCE_REPO", str(repo))

    def fake_run(cmd: list[str], **kwargs: Any) -> Any:
        result = MagicMock()
        if "refs/heads/integration" in cmd:
            result.stdout = "abc1234def5678\trefs/heads/integration\n"
        else:
            result.stdout = ""
        return result

    monkeypatch.setattr(subprocess, "run", fake_run)

    sha = resolve_reference_sha(info, tmp_path, network=False)
    assert sha == "abc1234def5678"


def test_resolve_ref_sha_falls_back_to_tag_ref_syntax(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When refs/heads/<rev> returns empty, try refs/tags/<rev>^{} next."""
    from autoskillit.cli._source_drift import resolve_reference_sha

    info = _make_install_info(commit_id="old111", requested_revision="v1.0.0")

    repo = tmp_path / "src_repo"
    (repo / "src" / "autoskillit").mkdir(parents=True)
    monkeypatch.setenv("AUTOSKILLIT_SOURCE_REPO", str(repo))

    captured_refs: list[str] = []

    def fake_run(cmd: list[str], **kwargs: Any) -> Any:
        result = MagicMock()
        ref = cmd[-1] if cmd else ""
        captured_refs.append(ref)
        if "refs/tags" in ref:
            result.stdout = "tagsha123456\trefs/tags/v1.0.0^{}\n"
        else:
            result.stdout = ""  # heads ref returns empty
        return result

    monkeypatch.setattr(subprocess, "run", fake_run)

    sha = resolve_reference_sha(info, tmp_path, network=False)

    assert sha == "tagsha123456"
    assert any("refs/heads" in r for r in captured_refs), (
        "refs/heads ref should have been tried first"
    )
    assert any("refs/tags" in r for r in captured_refs), (
        "refs/tags ref should be tried as fallback"
    )


def test_resolve_ref_sha_falls_back_to_github_api_when_no_source_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When no source repo is found, GitHub API is called via _fetch_with_cache."""
    from autoskillit.cli._source_drift import resolve_reference_sha

    monkeypatch.delenv("AUTOSKILLIT_SOURCE_REPO", raising=False)
    monkeypatch.chdir(tmp_path)  # No pyproject.toml here

    info = _make_install_info(commit_id="old111", requested_revision="integration")

    fetch_calls: list[str] = []

    def fake_fetch(url: str, *, home: Path, ttl: Any = None) -> dict | None:
        fetch_calls.append(url)
        return {"object": {"sha": "apisha123456"}}

    import autoskillit.cli._stale_check as _sc

    monkeypatch.setattr(_sc, "_fetch_with_cache", fake_fetch)

    sha = resolve_reference_sha(info, tmp_path, network=True)

    assert sha == "apisha123456"
    assert any("integration" in url for url in fetch_calls), (
        f"Expected API call for integration branch, got: {fetch_calls}"
    )


def test_resolve_ref_sha_short_circuits_exact_sha_equality(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When requested_revision exactly equals commit_id, return early without git or network."""
    from autoskillit.cli._source_drift import InstallInfo, InstallType, resolve_reference_sha

    commit = "abcdef1234567890abcdef1234567890"
    info = InstallInfo(
        install_type=InstallType.GIT_VCS,
        commit_id=commit,
        requested_revision=commit,  # exact match
        url="https://github.com/TalonT-Org/AutoSkillit.git",
        editable_source=None,
    )

    git_called = [False]

    def bad_run(cmd: list[str], **kwargs: Any) -> Any:
        git_called[0] = True
        raise AssertionError("git subprocess must not be called on exact SHA match")

    monkeypatch.setattr(subprocess, "run", bad_run)

    sha = resolve_reference_sha(info, tmp_path, network=True)
    assert sha == commit
    assert not git_called[0]


def test_resolve_ref_sha_does_not_short_circuit_on_hex_prefix_branch_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A branch named after a hex prefix of commit_id must NOT short-circuit."""
    from autoskillit.cli._source_drift import InstallInfo, InstallType, resolve_reference_sha

    # commit_id starts with "abc123", requested_revision IS "abc123" (a branch)
    # These are NOT equal (one is a branch name, other is a full SHA)
    commit_id = "abc12399999999999999999999999999"
    info = InstallInfo(
        install_type=InstallType.GIT_VCS,
        commit_id=commit_id,
        requested_revision="abc123",  # hex prefix branch name
        url="https://github.com/TalonT-Org/AutoSkillit.git",
        editable_source=None,
    )

    monkeypatch.delenv("AUTOSKILLIT_SOURCE_REPO", raising=False)
    monkeypatch.chdir(tmp_path)  # No source repo under CWD

    fetch_calls: list[str] = []

    def fake_fetch(url: str, *, home: Path, ttl: Any = None) -> dict | None:
        fetch_calls.append(url)
        return {"object": {"sha": "branchheadsha"}}

    import autoskillit.cli._stale_check as _sc

    monkeypatch.setattr(_sc, "_fetch_with_cache", fake_fetch)

    sha = resolve_reference_sha(info, tmp_path, network=True)

    assert sha == "branchheadsha", "Should have consulted git/network, not short-circuited"
    assert fetch_calls, "Network should have been called — no short-circuit for hex prefix branch"


def test_resolve_ref_sha_missing_requested_revision_returns_none(
    tmp_path: Path,
) -> None:
    """When requested_revision is None, return None and skip all resolution."""
    from autoskillit.cli._source_drift import InstallInfo, InstallType, resolve_reference_sha

    info = InstallInfo(
        install_type=InstallType.GIT_VCS,
        commit_id="abc1234",
        requested_revision=None,  # bare pip install with no @ref
        url="https://github.com/TalonT-Org/AutoSkillit.git",
        editable_source=None,
    )

    sha = resolve_reference_sha(info, tmp_path, network=True)
    assert sha is None


def test_resolve_ref_sha_fails_open_on_any_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On subprocess error, resolve_reference_sha returns None and logs DEBUG."""

    from autoskillit.cli._source_drift import resolve_reference_sha

    info = _make_install_info(commit_id="old111", requested_revision="integration")

    repo = tmp_path / "src_repo"
    (repo / "src" / "autoskillit").mkdir(parents=True)
    monkeypatch.setenv("AUTOSKILLIT_SOURCE_REPO", str(repo))

    def bad_run(cmd: list[str], **kwargs: Any) -> Any:
        raise subprocess.CalledProcessError(1, "git")

    monkeypatch.setattr(subprocess, "run", bad_run)

    # Patch API fallback to also fail
    import autoskillit.cli._stale_check as _sc

    monkeypatch.setattr(_sc, "_fetch_with_cache", lambda *a, **kw: None)

    sha = resolve_reference_sha(info, tmp_path, network=True)
    assert sha is None  # Fail-open: returns None, does not raise


# ---------------------------------------------------------------------------
# run_source_drift_check — hint output
# ---------------------------------------------------------------------------


def _setup_drift_check(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    install_type_str: str = "git-vcs",
    commit_id: str = "old111aaa",
    requested_revision: str = "integration",
    ref_sha: str = "new222bbb",
    is_tty: bool = False,
    editable_source: Path | None = None,
) -> io.StringIO:
    """Set up mocks for run_source_drift_check and return a captured stdout."""
    import autoskillit.cli._source_drift as _sd
    import autoskillit.cli._stale_check as _sc
    from autoskillit.cli._source_drift import InstallInfo, InstallType

    install_type = InstallType(install_type_str)
    fake_info = InstallInfo(
        install_type=install_type,
        commit_id=commit_id,
        requested_revision=requested_revision,
        url="https://github.com/TalonT-Org/AutoSkillit.git",
        editable_source=editable_source,
    )

    monkeypatch.setattr(_sd, "detect_install", lambda: fake_info)
    monkeypatch.setattr(_sd, "resolve_reference_sha", lambda info, home, **kw: ref_sha)
    monkeypatch.setattr(_sc, "_read_dismiss_state", lambda home: {})
    monkeypatch.setattr(_sc, "_is_drift_dismissed", lambda state, installed, ref: False)
    monkeypatch.setattr(_sc, "_write_dismiss_state", lambda home, state: None)
    monkeypatch.setattr(_sd.subprocess, "run", MagicMock())

    fake_stdout = io.StringIO()
    fake_stdin = io.StringIO("n\n")
    monkeypatch.setattr(fake_stdin, "isatty", lambda: is_tty)
    monkeypatch.setattr(fake_stdout, "isatty", lambda: is_tty)
    monkeypatch.setattr(_sd.sys, "stdin", fake_stdin)
    monkeypatch.setattr(_sd.sys, "stdout", fake_stdout)

    monkeypatch.delenv("AUTOSKILLIT_SKIP_SOURCE_DRIFT_CHECK", raising=False)
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.delenv("CI", raising=False)

    return fake_stdout


def test_drift_gate_prints_install_dev_hint_for_integration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When drift is detected for integration branch, hint includes 'task install-dev'."""
    from autoskillit.cli._source_drift import run_source_drift_check

    fake_stdout = _setup_drift_check(monkeypatch, tmp_path, requested_revision="integration")

    run_source_drift_check(home=tmp_path)

    output = fake_stdout.getvalue()
    assert "task install-dev" in output, f"Expected 'task install-dev' in output, got: {output!r}"


def test_drift_gate_prints_curl_install_hint_for_stable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When drift is detected for stable branch, hint includes curl install.sh."""
    from autoskillit.cli._source_drift import run_source_drift_check

    fake_stdout = _setup_drift_check(monkeypatch, tmp_path, requested_revision="stable")

    run_source_drift_check(home=tmp_path)

    output = fake_stdout.getvalue()
    assert "install.sh" in output, f"Expected 'install.sh' in output, got: {output!r}"
    assert "curl" in output, f"Expected 'curl' in output, got: {output!r}"


def test_drift_gate_prints_install_worktree_hint_for_editable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When local-editable install drifts, hint includes 'task install-worktree'."""
    from autoskillit.cli._source_drift import run_source_drift_check

    fake_stdout = _setup_drift_check(
        monkeypatch,
        tmp_path,
        install_type_str="local-editable",
        commit_id=None,
        requested_revision=None,
        editable_source=tmp_path,
    )

    run_source_drift_check(home=tmp_path)

    output = fake_stdout.getvalue()
    assert "install-worktree" in output, (
        f"Expected 'task install-worktree' in output, got: {output!r}"
    )


def test_drift_gate_prints_generic_hint_for_unknown_ref(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When drift is detected for an unrecognised revision, a generic hint is printed."""
    from autoskillit.cli._source_drift import run_source_drift_check

    fake_stdout = _setup_drift_check(
        monkeypatch, tmp_path, requested_revision="some-custom-branch"
    )

    run_source_drift_check(home=tmp_path)

    output = fake_stdout.getvalue()
    assert output.strip(), "Expected some output for unknown-ref drift"
    # No specific command should be mentioned for unknown refs
    assert "task install-dev" not in output
    assert "install.sh" not in output


def test_drift_gate_offers_yn_prompt_on_tty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On a TTY, the gate offers a [Y/n] update prompt."""
    from autoskillit.cli._source_drift import run_source_drift_check

    _setup_drift_check(monkeypatch, tmp_path, is_tty=True)
    # fake_stdin is set to "n\n" by _setup_drift_check
    input_calls: list[str] = []

    monkeypatch.setattr("builtins.input", lambda prompt="": (input_calls.append(prompt), "n")[1])

    run_source_drift_check(home=tmp_path)

    assert input_calls, "input() should have been called on TTY"
    assert any("[Y/n]" in p or "Update" in p for p in input_calls), (
        f"Expected [Y/n] prompt, got: {input_calls}"
    )


def test_drift_gate_non_tty_prints_warning_and_continues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On non-TTY, warning is printed but no input() call is made."""
    from autoskillit.cli._source_drift import run_source_drift_check

    fake_stdout = _setup_drift_check(monkeypatch, tmp_path, is_tty=False)

    input_calls: list[str] = []
    monkeypatch.setattr("builtins.input", lambda prompt="": (input_calls.append(prompt), "")[1])

    run_source_drift_check(home=tmp_path)

    assert not input_calls, f"input() must not be called on non-TTY, got: {input_calls}"
    output = fake_stdout.getvalue()
    assert output.strip(), "Expected some output on non-TTY"


# ---------------------------------------------------------------------------
# run_source_drift_check — env var bypass guards
# ---------------------------------------------------------------------------


def test_skip_env_var_bypasses_drift_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    """AUTOSKILLIT_SKIP_SOURCE_DRIFT_CHECK=1 causes early return with no detection work."""
    import autoskillit.cli._source_drift as _sd

    monkeypatch.setenv("AUTOSKILLIT_SKIP_SOURCE_DRIFT_CHECK", "1")
    detect_calls: list[int] = []
    monkeypatch.setattr(_sd, "detect_install", lambda: detect_calls.append(1) or None)

    _sd.run_source_drift_check()

    assert not detect_calls, "detect_install must not be called when skip env var is set"


def test_claudecode_env_var_bypasses_drift_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    """CLAUDECODE=1 causes early return (headless/MCP sessions skip the gate)."""
    import autoskillit.cli._source_drift as _sd

    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.delenv("AUTOSKILLIT_SKIP_SOURCE_DRIFT_CHECK", raising=False)
    detect_calls: list[int] = []
    monkeypatch.setattr(_sd, "detect_install", lambda: detect_calls.append(1) or None)

    _sd.run_source_drift_check()

    assert not detect_calls, "detect_install must not be called when CLAUDECODE=1"


def test_ci_env_var_bypasses_drift_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    """CI=1 causes early return (belt-and-suspenders for generic CI environments)."""
    import autoskillit.cli._source_drift as _sd

    monkeypatch.setenv("CI", "1")
    monkeypatch.delenv("AUTOSKILLIT_SKIP_SOURCE_DRIFT_CHECK", raising=False)
    monkeypatch.delenv("CLAUDECODE", raising=False)
    detect_calls: list[int] = []
    monkeypatch.setattr(_sd, "detect_install", lambda: detect_calls.append(1) or None)

    _sd.run_source_drift_check()

    assert not detect_calls, "detect_install must not be called when CI=1"


# ---------------------------------------------------------------------------
# run_source_drift_check — dismissal
# ---------------------------------------------------------------------------


def test_drift_dismissal_12h_window_sha_keyed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dismissal is SHA-keyed with a 12h window.

    dismiss at T0 → suppressed at T+11h59m (same SHAs) → fires at T+12h01m
    → fires at T+1h with different commit_id.
    """
    from autoskillit.cli._stale_check import (
        _DISMISS_WINDOW,
        _is_drift_dismissed,
        _write_dismiss_state,
    )

    installed = "abc123"
    reference = "def456"

    # Write dismissal at T0
    state: dict[str, object] = {}
    _write_dismiss_state(tmp_path, state)  # Ensure dir exists

    now = datetime.now(UTC)
    dismissed_entry = {
        "dismissed_at": now.isoformat(),
        "installed_sha": installed,
        "reference_sha": reference,
    }
    state_with_dismiss: dict[str, object] = {"source_drift": dismissed_entry}

    # Within 12h window, same SHAs → suppressed
    assert _is_drift_dismissed(state_with_dismiss, installed, reference) is True

    # Same SHAs but dismissed_at is T+11h59m (still within window)
    within_window = now - timedelta(hours=11, minutes=59)
    state_within: dict[str, object] = {
        "source_drift": {
            "dismissed_at": within_window.isoformat(),
            "installed_sha": installed,
            "reference_sha": reference,
        }
    }
    assert _is_drift_dismissed(state_within, installed, reference) is True

    # Past 12h window → fires again
    past_window = now - (_DISMISS_WINDOW + timedelta(minutes=1))
    state_expired: dict[str, object] = {
        "source_drift": {
            "dismissed_at": past_window.isoformat(),
            "installed_sha": installed,
            "reference_sha": reference,
        }
    }
    assert _is_drift_dismissed(state_expired, installed, reference) is False

    # Within window but different commit_id → fires
    state_diff_sha: dict[str, object] = {
        "source_drift": {
            "dismissed_at": now.isoformat(),
            "installed_sha": "different_sha",  # different commit_id
            "reference_sha": reference,
        }
    }
    assert _is_drift_dismissed(state_diff_sha, installed, reference) is False


# ---------------------------------------------------------------------------
# run_source_drift_check — fail-open (never raises)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "exc",
    [
        OSError("disk full"),
        PermissionError("denied"),
        TimeoutError("timeout"),
        json.JSONDecodeError("bad", "", 0),
        ValueError("shape"),
        KeyError("missing"),
        subprocess.CalledProcessError(1, "git"),
        FileNotFoundError("git"),
        httpx.ConnectError("offline"),
        httpx.TimeoutException("slow"),
    ],
)
def test_drift_gate_never_raises_on_error(
    exc: Exception, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """run_source_drift_check never raises on any Exception subclass.

    Exception is injected at four fault-injection points:
    1. detect_install
    2. resolve_reference_sha
    3. _read_dismiss_state
    4. _write_dismiss_state (via the dismiss path)
    """
    import autoskillit.cli._source_drift as _sd
    import autoskillit.cli._stale_check as _sc

    monkeypatch.delenv("AUTOSKILLIT_SKIP_SOURCE_DRIFT_CHECK", raising=False)
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.delenv("CI", raising=False)

    # Fault point 1: detect_install
    monkeypatch.setattr(_sd, "detect_install", lambda: (_ for _ in ()).throw(type(exc)(str(exc))))
    _sd.run_source_drift_check(home=tmp_path)  # must not raise

    # Fault point 2: resolve_reference_sha (detect_install succeeds this time)
    from autoskillit.cli._source_drift import InstallInfo, InstallType

    good_info = InstallInfo(
        install_type=InstallType.GIT_VCS,
        commit_id="old111",
        requested_revision="integration",
        url=None,
        editable_source=None,
    )
    monkeypatch.setattr(_sd, "detect_install", lambda: good_info)
    monkeypatch.setattr(
        _sd,
        "resolve_reference_sha",
        lambda info, home, **kw: (_ for _ in ()).throw(type(exc)(str(exc))),
    )
    _sd.run_source_drift_check(home=tmp_path)  # must not raise

    # Fault point 3: _read_dismiss_state (resolve succeeds, SHAs differ)
    monkeypatch.setattr(_sd, "resolve_reference_sha", lambda info, home, **kw: "new222")
    monkeypatch.setattr(
        _sc,
        "_read_dismiss_state",
        lambda home: (_ for _ in ()).throw(type(exc)(str(exc))),
    )
    _sd.run_source_drift_check(home=tmp_path)  # must not raise

    # Fault point 4: _write_dismiss_state (dismiss path, non-TTY so no prompt)
    monkeypatch.setattr(_sc, "_read_dismiss_state", lambda home: {})
    monkeypatch.setattr(_sc, "_is_drift_dismissed", lambda state, installed, ref: False)
    monkeypatch.setattr(
        _sc,
        "_write_dismiss_state",
        lambda home, state: (_ for _ in ()).throw(type(exc)(str(exc))),
    )
    fake_stdout = io.StringIO()
    fake_stdin = io.StringIO("")
    monkeypatch.setattr(fake_stdin, "isatty", lambda: False)
    monkeypatch.setattr(fake_stdout, "isatty", lambda: False)
    monkeypatch.setattr(_sd.sys, "stdin", fake_stdin)
    monkeypatch.setattr(_sd.sys, "stdout", fake_stdout)
    _sd.run_source_drift_check(home=tmp_path)  # must not raise


def test_drift_gate_only_catches_exception_not_base_exception() -> None:
    """run_source_drift_check must only catch Exception, not BaseException.

    This is an AST-level contract: the drift gate must not swallow
    KeyboardInterrupt or SystemExit.
    """
    src = Path(__file__).parent.parent.parent / "src" / "autoskillit" / "cli" / "_source_drift.py"
    if not src.exists():
        pytest.skip("Source tree unavailable")

    tree = ast.parse(src.read_text(encoding="utf-8"))
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            t = node.type
            if t is None:
                violations.append(f"Line {node.lineno}: bare except (catches BaseException)")
            elif isinstance(t, ast.Name) and t.id == "BaseException":
                violations.append(f"Line {node.lineno}: except BaseException")
            elif isinstance(t, ast.Attribute) and t.attr == "BaseException":
                violations.append(f"Line {node.lineno}: except BaseException")

    assert not violations, (
        "run_source_drift_check must not catch BaseException (would swallow KeyboardInterrupt):\n"
        + "\n".join(violations)
    )
