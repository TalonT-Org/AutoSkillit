"""Contract: the detection→repair loop closes — the incident's own end state
heals automatically instead of requiring a manual `autoskillit install`.

Sibling idiom to tests/contracts/test_install_state_consistency.py. Covers
T-C3 (startup repair heals a stale cache) and T-C4 (CLI startup obligation
observer, both expected_version branches). T-C2's failure matrix belongs to
the canonical update-transaction suite; C-I4 belongs to the hook-registry
contract suite.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.medium]


def _publish_cache_incarnation(
    cache_dir: Path,
    version: str,
    *,
    broken: bool,
    logical_name: str = "guards/quota_guard",
) -> Path:
    """Write a minimal <version> incarnation with a real/broken hooks.json."""
    version_dir = cache_dir / version
    hooks_dir = version_dir / "hooks"
    hooks_dir.mkdir(parents=True)
    (hooks_dir / "_dispatch.py").write_text("# dispatcher stub")
    command = (
        f"python3 /deleted/venv/hooks/_dispatch.py {logical_name}"
        if broken
        else f'python3 "${{CLAUDE_PLUGIN_ROOT}}/hooks/_dispatch.py" {logical_name}'
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
    from autoskillit.core import ArtifactLease, installed_plugin_artifact_lease_path
    from autoskillit.workspace._projected_artifact._manifest_publication import (
        write_installed_plugin_artifact_manifest_locked,
    )

    with ArtifactLease.acquire_exclusive(
        installed_plugin_artifact_lease_path(version_dir),
        blocking=True,
    ):
        write_installed_plugin_artifact_manifest_locked(
            version_dir,
            semantic_key=f"autoskillit@autoskillit-local:{version}",
            action="publish",
        )
    return version_dir


def test_obligation_clear_uses_compare_and_delete(tmp_path: Path) -> None:
    from autoskillit.workspace import clear_obligation, read_obligation, write_obligation

    older = write_obligation(tmp_path, previous_version="1.0.0", originating_phase="older-update")
    newer = write_obligation(tmp_path, previous_version="1.1.0", originating_phase="newer-update")

    assert clear_obligation(tmp_path, expected=older) is False
    assert read_obligation(tmp_path) == newer
    assert clear_obligation(tmp_path, expected=newer) is True
    assert read_obligation(tmp_path) is None


def test_obligation_clear_failure_is_observable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from autoskillit.workspace import clear_obligation, read_obligation, write_obligation

    obligation = write_obligation(tmp_path, previous_version="1.0.0", originating_phase="upgrade")
    original_unlink = Path.unlink

    def fail_obligation_unlink(path: Path, *args: object, **kwargs: object) -> None:
        if path.name == "update_obligation.json":
            raise PermissionError("denied")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_obligation_unlink)

    assert clear_obligation(tmp_path, expected=obligation) is False
    assert read_obligation(tmp_path) == obligation


def test_empty_persisted_expected_version_degrades_to_unknown(tmp_path: Path) -> None:
    from autoskillit.workspace import (
        read_obligation,
        update_obligation_expected_version,
        write_obligation,
    )

    obligation = write_obligation(tmp_path, previous_version="1.0.0", originating_phase="upgrade")
    updated = update_obligation_expected_version(
        tmp_path, expected=obligation, expected_version="  "
    )

    assert updated is not None
    persisted = read_obligation(tmp_path)
    assert persisted is not None
    assert persisted.expected_version is None


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
    from autoskillit.core import directory_tree_digest, installed_plugin_artifact_manifest_path
    from autoskillit.hook_registry import validate_plugin_cache_hooks
    from autoskillit.server._lifespan import run_startup_hook_health_check

    home = tmp_path
    cache_dir = home / ".claude" / "plugins" / "cache" / "autoskillit-local" / "autoskillit"
    version_dir = _publish_cache_incarnation(cache_dir, "1.0.0", broken=True)
    manifest_path = installed_plugin_artifact_manifest_path(version_dir)
    manifest_before = json.loads(manifest_path.read_text())

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
    manifest_after = json.loads(manifest_path.read_text())
    assert manifest_after["artifact_digest"] == directory_tree_digest(version_dir)
    assert manifest_after["artifact_digest"] != manifest_before["artifact_digest"]


def test_t_c3_repair_preserves_version_owned_logical_hooks(tmp_path: Path) -> None:
    from autoskillit.workspace._projected_artifact._hook_repair import (
        repair_broken_plugin_cache_hooks,
    )

    cache_dir = tmp_path / ".claude/plugins/cache/autoskillit-local/autoskillit"
    version_dir = _publish_cache_incarnation(
        cache_dir,
        "1.0.0",
        broken=True,
        logical_name="legacy/version_specific",
    )

    outcomes = repair_broken_plugin_cache_hooks(cache_dir)

    payload = json.loads((version_dir / "hooks/hooks.json").read_text())
    command = payload["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    assert outcomes[0].repaired is True
    assert command.endswith(" legacy/version_specific")


def test_t_c3_manifest_failure_rolls_back_hooks_and_manifest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from autoskillit.core import installed_plugin_artifact_manifest_path
    from autoskillit.workspace._projected_artifact import _hook_repair as repair_module

    cache_dir = tmp_path / ".claude/plugins/cache/autoskillit-local/autoskillit"
    version_dir = _publish_cache_incarnation(cache_dir, "1.0.0", broken=True)
    hooks_path = version_dir / "hooks/hooks.json"
    manifest_path = installed_plugin_artifact_manifest_path(version_dir)
    original_hooks = hooks_path.read_text()
    original_manifest = manifest_path.read_text()
    monkeypatch.setattr(
        repair_module,
        "write_installed_plugin_artifact_manifest_locked",
        MagicMock(side_effect=RuntimeError("manifest write failed")),
    )

    outcomes = repair_module.repair_broken_plugin_cache_hooks(cache_dir)

    assert outcomes[0].repaired is False
    assert "manifest write failed" in (outcomes[0].skipped_reason or "")
    assert hooks_path.read_text() == original_hooks
    assert manifest_path.read_text() == original_manifest


def test_t_c3_repair_refuses_to_bless_unrelated_tampering(tmp_path: Path) -> None:
    from autoskillit.workspace._projected_artifact._hook_repair import (
        repair_broken_plugin_cache_hooks,
    )

    cache_dir = tmp_path / ".claude/plugins/cache/autoskillit-local/autoskillit"
    version_dir = _publish_cache_incarnation(cache_dir, "1.0.0", broken=True)
    hooks_path = version_dir / "hooks/hooks.json"
    original_hooks = hooks_path.read_text()
    (version_dir / "skills" / "tampered").mkdir(parents=True)
    (version_dir / "skills" / "tampered" / "SKILL.md").write_text("modified")

    outcomes = repair_broken_plugin_cache_hooks(cache_dir)

    assert outcomes[0].repaired is False
    assert "content digest mismatch" in (outcomes[0].skipped_reason or "")
    assert hooks_path.read_text() == original_hooks


def test_t_c3_repair_rejects_unsafe_logical_hook_names(tmp_path: Path) -> None:
    from autoskillit.workspace._projected_artifact._hook_repair import (
        repair_broken_plugin_cache_hooks,
    )

    cache_dir = tmp_path / ".claude/plugins/cache/autoskillit-local/autoskillit"
    version_dir = _publish_cache_incarnation(
        cache_dir,
        "1.0.0",
        broken=True,
        logical_name="legacy/version_specific;touch",
    )
    hooks_path = version_dir / "hooks/hooks.json"
    original_hooks = hooks_path.read_text()

    outcomes = repair_broken_plugin_cache_hooks(cache_dir)

    assert outcomes[0].repaired is False
    assert "invalid logical hook name" in (outcomes[0].skipped_reason or "")
    assert hooks_path.read_text() == original_hooks


def test_t_c3_missing_dispatcher_rolls_back_failed_repair(tmp_path: Path) -> None:
    from autoskillit.core import (
        _AUTOSKILLIT_PLUGIN_KEY,
        ArtifactLease,
        installed_plugin_artifact_lease_path,
        installed_plugin_artifact_manifest_path,
        installed_plugin_semantic_key,
    )
    from autoskillit.workspace._projected_artifact._hook_repair import (
        repair_broken_plugin_cache_hooks,
    )
    from autoskillit.workspace._projected_artifact._manifest_publication import (
        write_installed_plugin_artifact_manifest_locked,
    )

    cache_dir = tmp_path / ".claude/plugins/cache/autoskillit-local/autoskillit"
    version_dir = _publish_cache_incarnation(cache_dir, "1.0.0", broken=True)
    hooks_path = version_dir / "hooks/hooks.json"
    manifest_path = installed_plugin_artifact_manifest_path(version_dir)
    (version_dir / "hooks/_dispatch.py").unlink()
    with ArtifactLease.acquire_exclusive(
        installed_plugin_artifact_lease_path(version_dir),
        blocking=True,
    ):
        write_installed_plugin_artifact_manifest_locked(
            version_dir,
            semantic_key=installed_plugin_semantic_key(
                _AUTOSKILLIT_PLUGIN_KEY,
                "1.0.0",
            ),
            action="publish",
        )
    original_hooks = hooks_path.read_text()
    original_manifest = manifest_path.read_text()

    outcomes = repair_broken_plugin_cache_hooks(cache_dir)

    assert outcomes[0].repaired is False
    assert "remain after repair" in (outcomes[0].skipped_reason or "")
    assert hooks_path.read_text() == original_hooks
    assert manifest_path.read_text() == original_manifest


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
    obligation = write_obligation(
        home, previous_version="1.0.0", originating_phase="upgrade-subprocess-gate"
    )
    update_obligation_expected_version(home, expected=obligation, expected_version="1.1.0")

    captured_specs: list[object] = []

    def fake_verify(spec: object) -> MagicMock:
        captured_specs.append(spec)
        return MagicMock(identity=MagicMock(semantic_key="x"), findings=(), lease=None)

    calls: list[list[str]] = []

    def runner(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0)

    original = m.verify_installed_plugin_artifact
    m.verify_installed_plugin_artifact = fake_verify
    try:
        result = m.attempt_obligation_repair(
            home,
            environment={},
            process_runner=runner,
            entrypoint=Path("autoskillit"),
        )
    finally:
        m.verify_installed_plugin_artifact = original

    assert result.outcome is m.ObligationRepairOutcome.CLEARED
    assert read_obligation(home) is None
    assert len(captured_specs) == 1
    assert captured_specs[0].expected_version == "1.1.0"  # type: ignore[attr-defined]
    assert calls == [["autoskillit", "install", "--maintenance-update"]]


def test_t_c4_expected_version_none_probes_then_verifies_exact_state(tmp_path: Path) -> None:
    """With expected_version None (probe never succeeded / backfill failed),
    the fresh version probe supplies the version for exact installed-state
    verification before the obligation can be cleared.
    """
    from autoskillit.cli.update import _obligation_repair as m
    from autoskillit.workspace import read_obligation, write_obligation

    home = tmp_path
    write_obligation(home, previous_version="1.0.0", originating_phase="upgrade-subprocess-gate")

    captured_specs: list[object] = []

    def fake_verify(spec: object) -> MagicMock:
        captured_specs.append(spec)
        return MagicMock(identity=MagicMock(semantic_key="x"), findings=(), lease=None)

    calls: list[list[str]] = []

    def runner(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        calls.append(list(cmd))
        stdout = "1.1.0\n" if cmd[-1] == "--version" else None
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout)

    original = m.verify_installed_plugin_artifact
    m.verify_installed_plugin_artifact = fake_verify
    try:
        result = m.attempt_obligation_repair(
            home,
            environment={},
            process_runner=runner,
            entrypoint=Path("autoskillit"),
        )
    finally:
        m.verify_installed_plugin_artifact = original

    assert result.outcome is m.ObligationRepairOutcome.CLEARED
    assert read_obligation(home) is None
    assert captured_specs[0].expected_version == "1.1.0"  # type: ignore[attr-defined]
    assert calls == [
        ["autoskillit", "install", "--maintenance-update"],
        ["autoskillit", "--version"],
    ]


def test_t_c4_unknown_version_requires_exact_installed_identity(tmp_path: Path) -> None:
    from autoskillit.cli.update import _obligation_repair as m
    from autoskillit.workspace import read_obligation, write_obligation

    write_obligation(
        tmp_path,
        previous_version="1.0.0",
        originating_phase="upgrade-subprocess-gate",
    )

    def runner(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        stdout = "1.1.0\n" if cmd[-1] == "--version" else None
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout)

    original = m.verify_installed_plugin_artifact
    m.verify_installed_plugin_artifact = lambda _spec: MagicMock(
        identity=None,
        findings=(),
        lease=None,
    )
    try:
        result = m.attempt_obligation_repair(
            tmp_path,
            environment={},
            process_runner=runner,
            entrypoint=Path("autoskillit"),
        )
    finally:
        m.verify_installed_plugin_artifact = original

    assert result.outcome is m.ObligationRepairOutcome.FAILED
    assert read_obligation(tmp_path) is not None


def test_t_c4_process_launch_error_is_reported_and_keeps_obligation(tmp_path: Path) -> None:
    from autoskillit.cli.update import _obligation_repair as m
    from autoskillit.workspace import read_obligation, write_obligation

    write_obligation(
        tmp_path,
        previous_version="1.0.0",
        originating_phase="upgrade-subprocess-gate",
    )

    def runner(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        raise OSError("entrypoint disappeared")

    result = m.attempt_obligation_repair(
        tmp_path,
        environment={},
        process_runner=runner,
        entrypoint=Path("/resolved/autoskillit"),
    )

    assert result.outcome is m.ObligationRepairOutcome.FAILED
    assert result.findings == (
        "Could not launch obligation repair install: entrypoint disappeared",
    )
    assert read_obligation(tmp_path) is not None


def test_t_c4_claudecode_defers_and_leaves_obligation_intact(tmp_path: Path) -> None:
    """Under CLAUDECODE=1 the repair defers with an instruction finding and
    leaves the obligation intact.
    """
    from autoskillit.cli.update._obligation_repair import (
        ObligationRepairOutcome,
        attempt_obligation_repair,
    )
    from autoskillit.workspace import read_obligation, write_obligation

    home = tmp_path
    write_obligation(home, previous_version="1.0.0", originating_phase="upgrade-subprocess-gate")

    result = attempt_obligation_repair(home, environment={"CLAUDECODE": "1"})

    assert result.outcome is ObligationRepairOutcome.DEFERRED
    assert result.findings
    assert read_obligation(home) is not None
