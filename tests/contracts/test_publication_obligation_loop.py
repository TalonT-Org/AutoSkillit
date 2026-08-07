"""Contract tests for persisted publication-obligation detection and repair."""

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
    from autoskillit.workspace._installed_artifact import (
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


@pytest.mark.parametrize(
    "contents",
    [
        "not json",
        "[]",
        json.dumps({"schema_version": 999}),
        json.dumps(
            {
                "schema_version": 1,
                "previous_version": ["1.0.0"],
                "expected_version": None,
                "written_at": "now",
                "originating_phase": "upgrade",
            }
        ),
    ],
)
def test_malformed_persisted_obligation_degrades_to_pending_unknown(
    tmp_path: Path,
    contents: str,
) -> None:
    from autoskillit.workspace import PublicationObligation, read_obligation

    path = tmp_path / ".autoskillit" / "update_obligation.json"
    path.parent.mkdir(parents=True)
    path.write_text(contents, encoding="utf-8")

    assert read_obligation(tmp_path) == PublicationObligation(
        previous_version="unknown",
        expected_version=None,
        written_at="unknown",
        originating_phase="unknown",
    )


def test_dangling_obligation_symlink_degrades_to_pending_unknown(tmp_path: Path) -> None:
    from autoskillit.workspace import PublicationObligation, read_obligation
    from autoskillit.workspace._update_obligation import _obligation_path

    path = _obligation_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.symlink_to(tmp_path / "missing-obligation.json")

    assert read_obligation(tmp_path) == PublicationObligation(
        previous_version="unknown",
        expected_version=None,
        written_at="unknown",
        originating_phase="unknown",
    )


def test_obligation_clear_maps_non_oserror_to_false(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autoskillit.workspace import clear_obligation, write_obligation

    obligation = write_obligation(
        tmp_path,
        previous_version="1.0.0",
        originating_phase="upgrade",
    )
    monkeypatch.setattr(
        "autoskillit.workspace._update_obligation.ArtifactLease.acquire_exclusive",
        MagicMock(side_effect=RuntimeError("lease backend failed")),
    )

    assert clear_obligation(tmp_path, expected=obligation) is False


# ---------------------------------------------------------------------------
# Per-incarnation hook repair and containment.
# ---------------------------------------------------------------------------


def test_startup_repair_heals_a_stale_cache(
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


def test_repair_preserves_version_owned_logical_hooks(tmp_path: Path) -> None:
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
    assert outcomes[0].status.value == "repaired"
    assert command.endswith(" legacy/version_specific")


def test_manifest_failure_rolls_back_hooks_and_manifest(
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

    assert outcomes[0].status.value == "failed"
    assert "manifest write failed" in (outcomes[0].detail or "")
    assert hooks_path.read_text() == original_hooks
    assert manifest_path.read_text() == original_manifest


def test_repair_refuses_to_bless_unrelated_tampering(tmp_path: Path) -> None:
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

    assert outcomes[0].status.value == "failed"
    assert "content digest mismatch" in (outcomes[0].detail or "")
    assert hooks_path.read_text() == original_hooks


def test_repair_rejects_unsafe_logical_hook_names(tmp_path: Path) -> None:
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

    assert outcomes[0].status.value == "failed"
    assert "invalid logical hook name" in (outcomes[0].detail or "")
    assert hooks_path.read_text() == original_hooks


def test_missing_dispatcher_rolls_back_failed_repair(tmp_path: Path) -> None:
    from autoskillit.core import (
        _AUTOSKILLIT_PLUGIN_KEY,
        ArtifactLease,
        installed_plugin_artifact_lease_path,
        installed_plugin_artifact_manifest_path,
        installed_plugin_semantic_key,
    )
    from autoskillit.workspace._installed_artifact import (
        write_installed_plugin_artifact_manifest_locked,
    )
    from autoskillit.workspace._projected_artifact._hook_repair import (
        repair_broken_plugin_cache_hooks,
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

    assert outcomes[0].status.value == "failed"
    assert "remain after repair" in (outcomes[0].detail or "")
    assert hooks_path.read_text() == original_hooks
    assert manifest_path.read_text() == original_manifest


def test_repair_skips_a_contended_lease(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
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
    assert outcomes[0].status.value == "contended"
    assert outcomes[0].detail == "lease contended"
    assert (version_dir / "hooks" / "hooks.json").read_text() == original_content


def test_hook_repair_does_not_follow_incarnation_symlinks(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    version_dir = _publish_cache_incarnation(tmp_path / "external", "1.0.0", broken=True)
    (cache_dir / "1.0.0").symlink_to(version_dir, target_is_directory=True)
    original = (version_dir / "hooks" / "hooks.json").read_text()

    from autoskillit.workspace import repair_broken_plugin_cache_hooks

    assert repair_broken_plugin_cache_hooks(cache_dir) == ()
    assert (version_dir / "hooks" / "hooks.json").read_text() == original


# ---------------------------------------------------------------------------
# Cross-process publication-obligation repair.
# ---------------------------------------------------------------------------


def test_expected_version_present_uses_full_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With expected_version present, verification resolves the current
    generation for that exact version and clears the obligation on success.
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

    gen_root = tmp_path / "generation-root"
    captured_generation_calls: list[tuple[object, str, str]] = []
    captured_identity_calls: list[object] = []

    def fake_resolve_current_generation(home_arg: object, plugin_ref: str, version: str) -> Path:
        captured_generation_calls.append((home_arg, plugin_ref, version))
        return gen_root

    def fake_read_identity(managed_path: object, **_kwargs: object) -> MagicMock:
        captured_identity_calls.append(managed_path)
        return MagicMock(semantic_key="x")

    monkeypatch.setattr(
        "autoskillit.core.resolve_current_generation", fake_resolve_current_generation
    )
    monkeypatch.setattr(
        "autoskillit.core.read_installed_plugin_artifact_identity", fake_read_identity
    )

    calls: list[list[str]] = []
    captured_kwargs: list[dict[str, object]] = []

    def runner(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        calls.append(list(cmd))
        captured_kwargs.append(kwargs)
        return subprocess.CompletedProcess(cmd, 0)

    result = m.attempt_obligation_repair(
        home,
        environment={},
        process_runner=runner,
        entrypoint=Path("autoskillit"),
    )

    assert result.outcome is m.ObligationRepairOutcome.CLEARED
    assert read_obligation(home) is None
    assert len(captured_generation_calls) == 1
    assert captured_generation_calls[0][2] == "1.1.0"
    assert captured_identity_calls == [gen_root]
    assert calls == [["autoskillit", "install", "--maintenance-update"]]
    assert captured_kwargs[0]["env"] == {"HOME": str(home)}


@pytest.mark.parametrize("persisted_version", [None, "not a version"])
def test_unknown_version_probes_then_verifies_exact_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    persisted_version: str | None,
) -> None:
    """Unknown or invalid persisted versions trigger a fresh exact probe."""
    from autoskillit.cli.update import _obligation_repair as m
    from autoskillit.workspace import (
        read_obligation,
        update_obligation_expected_version,
        write_obligation,
    )

    monkeypatch.setattr(
        "autoskillit.cli._install_info.detect_install",
        lambda: MagicMock(entrypoint=None),
    )

    home = tmp_path
    obligation = write_obligation(
        home,
        previous_version="1.0.0",
        originating_phase="upgrade-subprocess-gate",
    )
    if persisted_version is not None:
        update_obligation_expected_version(
            home,
            expected=obligation,
            expected_version=persisted_version,
        )
    entrypoint = tmp_path / "bin" / "autoskillit"
    entrypoint.parent.mkdir()
    entrypoint.write_text("#!/bin/sh\n")
    entrypoint.chmod(0o755)

    gen_root = tmp_path / "generation-root"
    captured_generation_calls: list[tuple[object, str, str]] = []

    def fake_resolve_current_generation(home_arg: object, plugin_ref: str, version: str) -> Path:
        captured_generation_calls.append((home_arg, plugin_ref, version))
        return gen_root

    def fake_read_identity(managed_path: object, **_kwargs: object) -> MagicMock:
        return MagicMock(semantic_key="x")

    monkeypatch.setattr(
        "autoskillit.core.resolve_current_generation", fake_resolve_current_generation
    )
    monkeypatch.setattr(
        "autoskillit.core.read_installed_plugin_artifact_identity", fake_read_identity
    )

    calls: list[list[str]] = []

    def runner(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        calls.append(list(cmd))
        stdout = "1.1.0\n" if cmd[-1] == "--version" else None
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout)

    result = m.attempt_obligation_repair(
        home,
        environment={"PATH": str(entrypoint.parent)},
        process_runner=runner,
    )

    assert result.outcome is m.ObligationRepairOutcome.CLEARED
    assert read_obligation(home) is None
    assert captured_generation_calls[0][2] == "1.1.0"
    assert calls == [
        [str(entrypoint), "install", "--maintenance-update"],
        [str(entrypoint), "--version"],
    ]


def test_unknown_version_requires_exact_installed_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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

    # No current generation resolves for the probed version — the exact
    # installed identity required to clear the obligation is unavailable.
    monkeypatch.setattr(
        "autoskillit.core.resolve_current_generation",
        lambda _home, _plugin_ref, _version: None,
    )

    result = m.attempt_obligation_repair(
        tmp_path,
        environment={},
        process_runner=runner,
        entrypoint=Path("autoskillit"),
    )

    assert result.outcome is m.ObligationRepairOutcome.FAILED
    assert read_obligation(tmp_path) is not None


def test_remaining_broken_hooks_keep_obligation(tmp_path: Path) -> None:
    from autoskillit.cli.update import _obligation_repair as m
    from autoskillit.workspace import read_obligation, write_obligation

    write_obligation(
        tmp_path,
        previous_version="1.0.0",
        originating_phase="upgrade-subprocess-gate",
    )
    original_validate = m.validate_plugin_cache_hooks
    m.validate_plugin_cache_hooks = lambda **_kwargs: ["broken command"]
    try:
        result = m.attempt_obligation_repair(
            tmp_path,
            environment={},
            process_runner=lambda cmd, **_kwargs: subprocess.CompletedProcess(cmd, 0),
            entrypoint=Path("autoskillit"),
        )
    finally:
        m.validate_plugin_cache_hooks = original_validate

    assert result.outcome is m.ObligationRepairOutcome.FAILED
    assert result.findings == ("1 broken hook command(s) remain after repair install",)
    assert read_obligation(tmp_path) is not None


def test_compare_and_clear_occurs_after_generation_identity_is_verified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The obligation is only cleared after the current generation resolves
    and its identity is read back — never before verification completes.
    """
    from autoskillit.cli.update import _obligation_repair as m
    from autoskillit.workspace import update_obligation_expected_version, write_obligation

    obligation = write_obligation(
        tmp_path,
        previous_version="1.0.0",
        originating_phase="upgrade-subprocess-gate",
    )
    update_obligation_expected_version(
        tmp_path,
        expected=obligation,
        expected_version="1.1.0",
    )
    events: list[str] = []
    gen_root = tmp_path / "generation-root"

    def fake_resolve_current_generation(_home: object, _plugin_ref: str, _version: str) -> Path:
        events.append("resolve")
        return gen_root

    def fake_read_identity(_managed_path: object, **_kwargs: object) -> MagicMock:
        events.append("verify")
        return MagicMock(semantic_key="x")

    monkeypatch.setattr(
        "autoskillit.core.resolve_current_generation", fake_resolve_current_generation
    )
    monkeypatch.setattr(
        "autoskillit.core.read_installed_plugin_artifact_identity", fake_read_identity
    )
    original_clear = m.clear_obligation
    m.clear_obligation = lambda *_args, **_kwargs: events.append("clear") or True
    try:
        result = m.attempt_obligation_repair(
            tmp_path,
            environment={},
            process_runner=lambda cmd, **_kwargs: subprocess.CompletedProcess(cmd, 0),
            entrypoint=Path("autoskillit"),
        )
    finally:
        m.clear_obligation = original_clear

    assert result.outcome is m.ObligationRepairOutcome.CLEARED
    assert events == ["resolve", "verify", "clear"]


def test_unexpected_verification_error_is_mapped_to_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from autoskillit.cli.update import _obligation_repair as m
    from autoskillit.workspace import (
        read_obligation,
        update_obligation_expected_version,
        write_obligation,
    )

    obligation = write_obligation(
        tmp_path,
        previous_version="1.0.0",
        originating_phase="upgrade-subprocess-gate",
    )
    update_obligation_expected_version(
        tmp_path,
        expected=obligation,
        expected_version="1.1.0",
    )

    def raise_backend_failure(_home: object, _plugin_ref: str, _version: str) -> Path:
        raise RuntimeError("verification backend failed")

    monkeypatch.setattr("autoskillit.core.resolve_current_generation", raise_backend_failure)

    result = m.attempt_obligation_repair(
        tmp_path,
        environment={},
        process_runner=lambda cmd, **_kwargs: subprocess.CompletedProcess(cmd, 0),
        entrypoint=Path("autoskillit"),
    )

    assert result.outcome is m.ObligationRepairOutcome.FAILED
    assert "verification backend failed" in result.findings[0]
    assert read_obligation(tmp_path) is not None


def test_process_launch_error_is_reported_and_keeps_obligation(tmp_path: Path) -> None:
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


def test_claudecode_defers_and_leaves_obligation_intact(tmp_path: Path) -> None:
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
