"""Contract: the detection→repair loop closes — the incident's own end state
heals automatically instead of requiring a manual `autoskillit install`.

Sibling idiom to tests/contracts/test_install_state_consistency.py. Covers
T-C2 (failure-path postcondition: hooks valid or repair owed), T-C3
(startup repair heals a stale cache), and T-C4 (CLI startup obligation
observer, both expected_version branches).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.medium]


def _publish_cache_incarnation(cache_dir: Path, version: str, *, broken: bool) -> Path:
    """Write a minimal <version> incarnation with a real/broken hooks.json."""
    version_dir = cache_dir / version
    hooks_dir = version_dir / "hooks"
    hooks_dir.mkdir(parents=True)
    (hooks_dir / "_dispatch.py").write_text("# dispatcher stub")
    command = (
        "python3 /deleted/venv/hooks/_dispatch.py guards/quota_guard"
        if broken
        else 'python3 "${CLAUDE_PLUGIN_ROOT}/hooks/_dispatch.py" guards/quota_guard'
    )
    payload = {
        "hooks": {
            "PreToolUse": [{"matcher": ".*", "hooks": [{"type": "command", "command": command}]}]
        }
    }
    (hooks_dir / "hooks.json").write_text(json.dumps(payload))
    metadata = version_dir / ".claude-plugin" / "plugin.json"
    metadata.parent.mkdir(parents=True)
    metadata.write_text(json.dumps({"name": "autoskillit", "version": version}))
    return version_dir


# ---------------------------------------------------------------------------
# T-C2 — failure-path postcondition contract.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fault",
    ["upgrade_nonzero_exit", "raising_probe", "child_failure_after_probe"],
)
def test_t_c2_hooks_valid_or_repair_owed_after_any_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fault: str
) -> None:
    """For every fault-injected failure of run_update_transaction, the
    system is never in the incident's state: broken-and-nobody-knows.

    Phase A's relocatable commands are a per-incarnation invariant
    independent of the transaction's outcome (the already-published
    incarnation's hooks.json never becomes broken as a *side effect* of a
    failed update attempt against it) — this test pins that a PUBLISHED
    incarnation's hooks stay valid regardless of what the transaction does,
    AND (for post-pivot failures) that the obligation journal records the
    republication debt.
    """
    from autoskillit.cli._install_info import InstallInfo, InstallType
    from autoskillit.cli.update._transaction import (
        UpdateTransactionOutcome,
        run_update_transaction,
    )
    from autoskillit.hook_registry import validate_plugin_cache_hooks
    from autoskillit.workspace import read_obligation

    home = tmp_path
    cache_dir = home / ".claude" / "plugins" / "cache" / "autoskillit-local" / "autoskillit"
    _publish_cache_incarnation(cache_dir, "1.0.0", broken=False)
    registry = home / ".claude" / "plugins" / "installed_plugins.json"
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text(
        json.dumps(
            {
                "version": 2,
                "plugins": {
                    "autoskillit@autoskillit-local": [{"installPath": str(cache_dir / "1.0.0")}]
                },
            }
        )
    )

    monkeypatch.setattr(
        "autoskillit.cli.update._transaction.detect_install",
        lambda: InstallInfo(InstallType.GIT_VCS, "abc", "stable", "https://x", None),
    )
    monkeypatch.setattr(
        "autoskillit.cli.update._transaction.upgrade_command",
        lambda _info: ["uv", "tool", "upgrade", "autoskillit"],
    )
    monkeypatch.setattr("autoskillit.cli.update._transaction.is_git_worktree", lambda _p: False)
    monkeypatch.setattr(
        "autoskillit.cli.update._transaction.is_git_main_checkout", lambda _p: False
    )

    calls: list[list[str]] = []

    def runner(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        calls.append(list(cmd))
        if fault == "upgrade_nonzero_exit" and len(calls) == 1:
            return subprocess.CompletedProcess(cmd, 7)
        if fault == "child_failure_after_probe" and len(calls) == 2:
            return subprocess.CompletedProcess(cmd, 9)
        return subprocess.CompletedProcess(cmd, 0)

    prober = (
        (lambda _i, _e, _r: (_ for _ in ()).throw(RuntimeError("simulated probe failure")))
        if fault == "raising_probe"
        else (lambda _i, _e, _r: "1.1.0")
    )

    result = run_update_transaction(
        home=home,
        base_env={"PATH": "/bin"},
        version_reader=lambda _name: "1.0.0",
        fresh_version_prober=prober,
        process_runner=runner,
    )

    assert result.outcome is not UpdateTransactionOutcome.COMPLETED, fault

    # The published incarnation's hooks must never be broken as a side
    # effect of a failed transaction that never touched it.
    assert validate_plugin_cache_hooks(cache_dir=cache_dir) == [], fault

    # Post-pivot failures (everything at/after the upgrade subprocess) must
    # leave the obligation recorded — the system knows publication is owed.
    assert read_obligation(home) is not None, fault


# ---------------------------------------------------------------------------
# T-C3 — startup repair heals a stale cache.
# ---------------------------------------------------------------------------


def test_t_c3_startup_repair_heals_a_stale_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A cache incarnation with legacy venv-absolute (broken) commands — the
    incident's exact on-disk state — is regenerated in relocatable form by
    the next server startup, and reported no longer broken.
    """
    from autoskillit.hook_registry import validate_plugin_cache_hooks
    from autoskillit.server._lifespan import run_startup_hook_health_check

    home = tmp_path
    cache_dir = home / ".claude" / "plugins" / "cache" / "autoskillit-local" / "autoskillit"
    _publish_cache_incarnation(cache_dir, "1.0.0", broken=True)

    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setattr(
        "autoskillit.server._lifespan.iter_all_scope_paths",
        lambda project_root=None: iter([]),
    )

    broken_before = run_startup_hook_health_check()
    assert broken_before, "must detect the broken legacy command"

    assert validate_plugin_cache_hooks(cache_dir=cache_dir) == [], (
        "startup must have repaired the incarnation in-process"
    )


def test_t_c3_repair_skips_a_contended_lease(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A held exclusive-conflicting lease: repair skips, diagnostic logged,
    hooks.json untouched.
    """
    from autoskillit.core import ArtifactLease, installed_plugin_artifact_lease_path
    from autoskillit.workspace._projected_artifact._hook_repair import (
        repair_broken_plugin_cache_hooks,
    )

    home = tmp_path
    cache_dir = home / ".claude" / "plugins" / "cache" / "autoskillit-local" / "autoskillit"
    version_dir = _publish_cache_incarnation(cache_dir, "1.0.0", broken=True)
    original_content = (version_dir / "hooks" / "hooks.json").read_text()

    lease_path = installed_plugin_artifact_lease_path(version_dir)
    held_lease = ArtifactLease.acquire_exclusive(lease_path, blocking=True)
    try:
        outcomes = repair_broken_plugin_cache_hooks(cache_dir)
    finally:
        held_lease.close()

    assert len(outcomes) == 1
    assert outcomes[0].repaired is False
    assert outcomes[0].skipped_reason == "lease contended"
    assert (version_dir / "hooks" / "hooks.json").read_text() == original_content


# ---------------------------------------------------------------------------
# T-C4 — CLI startup obligation observer.
# ---------------------------------------------------------------------------


def test_t_c4_expected_version_present_uses_full_verification(tmp_path: Path) -> None:
    """With expected_version present, verification includes
    verify_installed_plugin_artifact and clears the obligation on success.
    """
    from autoskillit.cli.update import _obligation_repair as m
    from autoskillit.workspace import (
        read_obligation,
        update_obligation_expected_version,
        write_obligation,
    )

    home = tmp_path
    write_obligation(home, previous_version="1.0.0", originating_phase="upgrade-subprocess-gate")
    update_obligation_expected_version(home, expected_version="1.1.0")

    captured_specs: list[object] = []

    def fake_verify(spec: object) -> MagicMock:
        captured_specs.append(spec)
        return MagicMock(identity=MagicMock(semantic_key="x"), findings=(), lease=None)

    def runner(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(cmd, 0)

    original = m.verify_installed_plugin_artifact
    m.verify_installed_plugin_artifact = fake_verify
    try:
        result = m.attempt_obligation_repair(home, environment={}, process_runner=runner)
    finally:
        m.verify_installed_plugin_artifact = original

    assert result.outcome == "cleared"
    assert read_obligation(home) is None
    assert len(captured_specs) == 1
    assert captured_specs[0].expected_version == "1.1.0"  # type: ignore[attr-defined]


def test_t_c4_expected_version_none_skips_install_state_spec(tmp_path: Path) -> None:
    """With expected_version None (probe never succeeded / backfill failed),
    verification must NOT construct an InstallStateSpec (whose
    expected_version is a required field, raising on empty) — instead
    verifies via token-aware hook validation plus a version-subprocess
    succeeding. No ValueError must surface.
    """
    from autoskillit.cli.update import _obligation_repair as m
    from autoskillit.workspace import read_obligation, write_obligation

    home = tmp_path
    write_obligation(home, previous_version="1.0.0", originating_phase="upgrade-subprocess-gate")

    def fail_if_called(spec: object) -> None:
        raise AssertionError("verify_installed_plugin_artifact must not be called")

    calls: list[list[str]] = []

    def runner(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0)

    original = m.verify_installed_plugin_artifact
    m.verify_installed_plugin_artifact = fail_if_called
    try:
        result = m.attempt_obligation_repair(home, environment={}, process_runner=runner)
    finally:
        m.verify_installed_plugin_artifact = original

    assert result.outcome == "cleared"
    assert read_obligation(home) is None
    assert calls[-1] == ["autoskillit", "--version"]


def test_t_c4_claudecode_defers_and_leaves_obligation_intact(tmp_path: Path) -> None:
    """Under CLAUDECODE=1 the repair defers with an instruction finding and
    leaves the obligation intact.
    """
    from autoskillit.cli.update._obligation_repair import attempt_obligation_repair
    from autoskillit.workspace import read_obligation, write_obligation

    home = tmp_path
    write_obligation(home, previous_version="1.0.0", originating_phase="upgrade-subprocess-gate")

    result = attempt_obligation_repair(home, environment={"CLAUDECODE": "1"})

    assert result.outcome == "deferred"
    assert result.findings
    assert read_obligation(home) is not None
