"""Tests for the shared success-gated update transaction."""

from __future__ import annotations

import importlib.metadata
import json
import shlex
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import structlog.testing

from autoskillit.cli._install_contract import (
    InstallFailureKind,
    InstallOutcome,
    InstallProcessStatus,
)
from autoskillit.cli._install_info import InstallInfo, InstallType
from autoskillit.cli.update._transaction import (
    IRREVERSIBLE_PIVOT_PHASE,
    UPDATE_TRANSACTION_PHASES,
    UpdateProcessStatus,
    UpdateTransactionOutcome,
    UpdateTransactionPhase,
    run_update_transaction,
)
from autoskillit.core import _AUTOSKILLIT_PLUGIN_KEY as _PLUGIN_REF
from autoskillit.core import Severity
from tests.fixtures.plugin_artifact_state import (
    PluginArtifactStateKind,
    build_plugin_artifact_state,
)

pytestmark = [pytest.mark.layer("cli"), pytest.mark.medium]


def _info() -> InstallInfo:
    return InstallInfo(
        install_type=InstallType.GIT_VCS,
        commit_id="abc123",
        requested_revision="stable",
        url="https://github.com/TalonT-Org/AutoSkillit.git",
        editable_source=None,
    )


def _register_plugin(home: Path, version: str = "1.0.0") -> Path:
    install_path = (
        home / ".claude" / "plugins" / "cache" / "autoskillit-local" / "autoskillit" / version
    )
    registry = home / ".claude" / "plugins" / "installed_plugins.json"
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text(
        json.dumps(
            {
                "version": 2,
                "plugins": {
                    _PLUGIN_REF: [{"installPath": str(install_path)}],
                },
            }
        ),
        encoding="utf-8",
    )
    return registry


def _phase_prefix(last_phase: UpdateTransactionPhase) -> tuple[UpdateTransactionPhase, ...]:
    index = UPDATE_TRANSACTION_PHASES.index(last_phase)
    return UPDATE_TRANSACTION_PHASES[: index + 1]


def _assert_terminal_history(
    result: Any,
    last_operational_phase: UpdateTransactionPhase,
) -> None:
    assert result.phase_history == (
        *_phase_prefix(last_operational_phase),
        UpdateTransactionPhase.RESULT_FINALIZATION,
    )


def _prepare(
    monkeypatch: pytest.MonkeyPatch,
    *,
    stub_git_checks: bool = True,
) -> None:
    monkeypatch.setattr("autoskillit.cli.update._transaction.detect_install", _info)
    monkeypatch.setattr(
        "autoskillit.cli.update._transaction.upgrade_command",
        lambda _info: ["uv", "tool", "upgrade", "autoskillit"],
    )
    if stub_git_checks:
        monkeypatch.setattr(
            "autoskillit.cli.update._transaction.is_git_worktree",
            lambda _path: False,
        )
        monkeypatch.setattr(
            "autoskillit.cli.update._transaction.is_git_main_checkout",
            lambda _path: False,
        )


def _create_caller_git_worktree(tmp_path: Path) -> Path:
    main_checkout = tmp_path / "caller-main"
    main_checkout.mkdir()
    subprocess.run(
        ["git", "init"],
        cwd=main_checkout,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=autoskillit@example.invalid",
            "-c",
            "user.name=AutoSkillit Test",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "--allow-empty",
            "--no-verify",
            "-m",
            "initial",
        ],
        cwd=main_checkout,
        check=True,
        capture_output=True,
    )
    caller_worktree = tmp_path / "caller-worktree"
    subprocess.run(
        ["git", "worktree", "add", "--detach", str(caller_worktree), "HEAD"],
        cwd=main_checkout,
        check=True,
        capture_output=True,
    )
    return caller_worktree.resolve()


def _assert_environment_not_logged(
    logs: list[dict[str, Any]],
    sensitive_env: dict[str, str],
) -> None:
    serialized = json.dumps(logs, sort_keys=True, default=str)
    for key, value in sensitive_env.items():
        assert key not in serialized
        assert value not in serialized


def _recording_success_runner(
    calls: list[list[str]],
) -> Callable[..., subprocess.CompletedProcess[Any]]:
    def runner(
        cmd: list[str],
        **kwargs: Any,
    ) -> subprocess.CompletedProcess[Any]:
        del kwargs
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0)

    return runner


def test_update_transaction_declares_exact_twelve_phase_pivot_contract() -> None:
    assert UPDATE_TRANSACTION_PHASES == (
        UpdateTransactionPhase.CALLER_ENV_CAPTURE,
        UpdateTransactionPhase.PRE_UPDATE_EVIDENCE_CAPTURE,
        UpdateTransactionPhase.PLUGIN_OBLIGATION_DERIVATION,
        UpdateTransactionPhase.SAFETY_CAPABILITY_PREFLIGHT,
        UpdateTransactionPhase.MAINTENANCE_CONTEXT_CONSTRUCTION,
        UpdateTransactionPhase.UPGRADE_SUBPROCESS_GATE,
        UpdateTransactionPhase.IRREVERSIBLE_PIVOT,
        UpdateTransactionPhase.FRESH_VERSION_METADATA_GATE,
        UpdateTransactionPhase.INSTALL_CHILD_INVOCATION,
        UpdateTransactionPhase.INSTALL_STATUS_RECONSTRUCTION,
        UpdateTransactionPhase.POST_UPDATE_ARTIFACT_VERIFICATION,
        UpdateTransactionPhase.RESULT_FINALIZATION,
    )
    assert len(UPDATE_TRANSACTION_PHASES) == 12
    assert IRREVERSIBLE_PIVOT_PHASE is UPDATE_TRANSACTION_PHASES[6]


def test_claudecode_with_existing_registration_defers_before_mutation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _prepare(monkeypatch)
    _register_plugin(tmp_path)
    calls: list[list[str]] = []
    sensitive_env = {
        "CLAUDECODE": "sentinel-required-refresh",
        "AUTOSKILLIT_SESSION_TYPE": "sentinel-session",
        "SECRET_TOKEN": "sentinel-deferral-secret",
    }

    with structlog.testing.capture_logs() as logs:
        result = run_update_transaction(
            home=tmp_path,
            base_env={"PATH": "/bin", **sensitive_env},
            version_reader=lambda _name: "1.0.0",
            process_runner=_recording_success_runner(calls),
        )

    assert result.outcome is UpdateTransactionOutcome.DEFERRED
    assert not calls
    assert not list((tmp_path / ".autoskillit").glob("update-maintenance-*"))
    _assert_terminal_history(result, UpdateTransactionPhase.SAFETY_CAPABILITY_PREFLIGHT)
    assert result.irreversible_pivot_crossed is False
    _assert_environment_not_logged(logs, sensitive_env)


def test_upgrade_failure_gates_install_and_cleans_cwd(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _prepare(monkeypatch)
    calls: list[list[str]] = []

    def runner(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[Any]:
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 7)

    result = run_update_transaction(
        home=tmp_path,
        base_env={"PATH": "/bin"},
        version_reader=lambda _name: "1.0.0",
        process_runner=runner,
    )

    assert result.outcome is UpdateTransactionOutcome.FAILED_UPGRADE
    assert calls == [["uv", "tool", "upgrade", "autoskillit"]]
    assert not list((tmp_path / ".autoskillit").glob("update-maintenance-*"))
    _assert_terminal_history(result, UpdateTransactionPhase.UPGRADE_SUBPROCESS_GATE)
    assert result.irreversible_pivot_crossed is False


def test_metadata_must_advance_before_install(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _prepare(monkeypatch)
    calls: list[list[str]] = []

    result = run_update_transaction(
        home=tmp_path,
        base_env={"PATH": "/bin"},
        version_reader=lambda _name: "1.0.0",
        process_runner=_recording_success_runner(calls),
    )

    assert result.outcome is UpdateTransactionOutcome.FAILED_UPGRADE
    assert len(calls) == 1
    _assert_terminal_history(result, UpdateTransactionPhase.FRESH_VERSION_METADATA_GATE)
    assert result.irreversible_pivot_crossed is True


@pytest.mark.parametrize(
    ("status", "expected", "expected_install"),
    [
        (
            InstallProcessStatus.DECLINED,
            UpdateTransactionOutcome.DECLINED,
            InstallOutcome.DECLINED,
        ),
        (
            InstallProcessStatus.DEFERRED,
            UpdateTransactionOutcome.DEFERRED,
            InstallOutcome.DEFERRED,
        ),
        (
            InstallProcessStatus.FAILED_PREFLIGHT,
            UpdateTransactionOutcome.FAILED_INSTALL,
            InstallOutcome.FAILED,
        ),
        (
            InstallProcessStatus.FAILED_CHILD,
            UpdateTransactionOutcome.FAILED_INSTALL,
            InstallOutcome.FAILED,
        ),
        (
            InstallProcessStatus.FAILED_POSTCONDITION,
            UpdateTransactionOutcome.FAILED_POSTCONDITION,
            InstallOutcome.FAILED,
        ),
        (
            InstallProcessStatus.RECOVERY_REQUIRED,
            UpdateTransactionOutcome.RECOVERY_REQUIRED,
            InstallOutcome.RECOVERY_REQUIRED,
        ),
        (
            InstallProcessStatus.INDETERMINATE,
            UpdateTransactionOutcome.INDETERMINATE,
            InstallOutcome.INDETERMINATE,
        ),
        (
            99,
            UpdateTransactionOutcome.INDETERMINATE,
            InstallOutcome.INDETERMINATE,
        ),
        (
            -15,
            UpdateTransactionOutcome.INDETERMINATE,
            InstallOutcome.INDETERMINATE,
        ),
    ],
)
def test_install_process_statuses_map_to_distinct_update_outcomes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    status: int,
    expected: UpdateTransactionOutcome,
    expected_install: InstallOutcome,
) -> None:
    _prepare(monkeypatch)
    versions = iter(["1.0.0", "1.1.0"])
    statuses = iter([0, int(status)])

    result = run_update_transaction(
        home=tmp_path,
        base_env={"PATH": "/bin"},
        version_reader=lambda _name: next(versions),
        process_runner=lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, next(statuses)),
    )

    assert result.outcome is expected
    assert result.expected_version == "1.1.0"
    assert result.install_result is not None
    assert result.install_result.outcome is expected_install
    _assert_terminal_history(
        result,
        UpdateTransactionPhase.INSTALL_STATUS_RECONSTRUCTION,
    )
    assert UpdateTransactionPhase.POST_UPDATE_ARTIFACT_VERIFICATION not in result.phase_history
    assert result.irreversible_pivot_crossed is True


def test_success_uses_sealed_env_explicit_cwd_and_maintenance_flags(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _prepare(monkeypatch)
    monkeypatch.setattr(
        "autoskillit.cli.update._transaction.verify_installed_plugin_artifact",
        lambda _spec: pytest.fail("no prior registration must not invent an obligation"),
    )
    versions = iter(["1.0.0", "1.1.0"])
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def runner(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[Any]:
        calls.append((list(cmd), kwargs))
        return subprocess.CompletedProcess(cmd, 0)

    result = run_update_transaction(
        home=tmp_path,
        base_env={
            "PATH": "/bin",
            "CLAUDECODE": "1",
            "AUTOSKILLIT_AGENT_BACKEND__BACKEND": "codex",
        },
        version_reader=lambda _name: next(versions),
        process_runner=runner,
    )

    assert result.outcome is UpdateTransactionOutcome.COMPLETED
    assert result.install_result is not None
    assert result.install_result.outcome is InstallOutcome.NOT_REQUIRED
    assert len(calls) == 2
    assert calls[0][1]["cwd"] == calls[1][1]["cwd"]
    assert str(calls[0][1]["cwd"]).startswith(str(tmp_path / ".autoskillit"))
    assert not list((tmp_path / ".autoskillit").glob("update-maintenance-*"))
    for _, kwargs in calls:
        env = kwargs["env"]
        assert env["AUTOSKILLIT_SKIP_STALE_CHECK"] == "1"
        assert env["AUTOSKILLIT_SKIP_UPDATE_CHECK"] == "1"
        assert "CLAUDECODE" not in env
        assert "AUTOSKILLIT_AGENT_BACKEND__BACKEND" not in env
    install_command = calls[1][0]
    assert install_command[:2] == ["autoskillit", "install"]
    assert "--maintenance-update" in install_command
    assert install_command[install_command.index("--expected-version") + 1] == "1.1.0"
    assert "--require-registered-plugin" not in install_command
    assert result.phase_history == UPDATE_TRANSACTION_PHASES
    assert result.irreversible_pivot_crossed is True


@pytest.mark.parametrize(
    "backend_override",
    [
        "AUTOSKILLIT_AGENT_BACKEND",
        "AUTOSKILLIT_AGENT_BACKEND__BACKEND",
    ],
)
def test_success_from_real_worktree_seals_env_and_uses_home_maintenance_cwd(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    backend_override: str,
) -> None:
    _prepare(monkeypatch, stub_git_checks=False)
    monkeypatch.setattr(
        "autoskillit.cli.update._transaction.verify_installed_plugin_artifact",
        lambda _spec: pytest.fail("no prior registration must not invent an obligation"),
    )
    caller_worktree = _create_caller_git_worktree(tmp_path)
    caller_toplevel = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=caller_worktree,
        check=True,
        capture_output=True,
        text=True,
    )
    assert Path(caller_toplevel.stdout.strip()).resolve() == caller_worktree
    monkeypatch.chdir(caller_worktree)

    home = (tmp_path / "authority-home").resolve()
    approved_base_env = {
        "HOME": str(home),
        "PATH": "/bin",
        "TERM": "xterm-256color",
        "XDG_CONFIG_HOME": str(home / "xdg-config"),
    }
    sensitive_env = {
        "CLAUDECODE": "sentinel-success-claudecode",
        backend_override: f"sentinel-{backend_override.lower()}",
        "AUTOSKILLIT_SESSION_TYPE": "sentinel-session",
        "AUTOSKILLIT_CAMPAIGN_ID": "sentinel-campaign",
        "AUTOSKILLIT_ORDER_ID": "sentinel-order",
        "PYTHONPATH": "sentinel-pythonpath",
        "SECRET_TOKEN": "sentinel-success-secret",
        "AUTOSKILLIT_SKIP_STALE_CHECK": "sentinel-parent-stale-skip",
        "AUTOSKILLIT_SKIP_UPDATE_CHECK": "sentinel-parent-update-skip",
    }
    expected_child_env = {
        **approved_base_env,
        "AUTOSKILLIT_SKIP_STALE_CHECK": "1",
        "AUTOSKILLIT_SKIP_UPDATE_CHECK": "1",
    }
    versions = iter(["1.0.0", "1.1.0"])
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def runner(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[Any]:
        calls.append((list(cmd), kwargs))
        return subprocess.CompletedProcess(cmd, 0)

    with structlog.testing.capture_logs() as logs:
        result = run_update_transaction(
            home=home,
            base_env={**approved_base_env, **sensitive_env},
            version_reader=lambda _name: next(versions),
            process_runner=runner,
        )

    assert result.outcome is UpdateTransactionOutcome.COMPLETED
    assert len(calls) == 2
    for _, kwargs in calls:
        assert kwargs["check"] is False
        assert dict(kwargs["env"]) == expected_child_env

    maintenance_cwds = [Path(kwargs["cwd"]).resolve() for _, kwargs in calls]
    assert maintenance_cwds[0] == maintenance_cwds[1]
    maintenance_cwd = maintenance_cwds[0]
    assert maintenance_cwd.parent == (home / ".autoskillit").resolve()
    assert not maintenance_cwd.is_relative_to(caller_worktree)
    assert not maintenance_cwd.exists()
    _assert_environment_not_logged(logs, sensitive_env)


def test_codex_caller_with_old_claude_registration_completes_only_after_matching_publication(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _prepare(monkeypatch)
    registry = _register_plugin(tmp_path)
    versions = iter(["1.0.0", "1.1.0"])
    calls: list[list[str]] = []
    lease = SimpleNamespace(closed=False)
    lease.close = lambda: setattr(lease, "closed", True)

    def runner(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[Any]:
        calls.append(list(cmd))
        if len(calls) == 2:
            _register_plugin(tmp_path, "1.1.0")
        return subprocess.CompletedProcess(cmd, 0)

    captured_specs: list[Any] = []

    def verify(spec: Any) -> Any:
        captured_specs.append(spec)
        registry_state = json.loads(registry.read_text(encoding="utf-8"))
        fresh_path = registry_state["plugins"][_PLUGIN_REF][0]["installPath"]
        assert fresh_path.endswith("/1.1.0")
        return SimpleNamespace(
            identity=SimpleNamespace(semantic_key=f"{_PLUGIN_REF}:1.1.0"),
            findings=(),
            lease=lease,
        )

    monkeypatch.setattr(
        "autoskillit.cli.update._transaction.verify_installed_plugin_artifact",
        verify,
    )
    result = run_update_transaction(
        home=tmp_path,
        base_env={
            "PATH": "/bin",
            "AUTOSKILLIT_AGENT_BACKEND__BACKEND": "codex",
        },
        version_reader=lambda _name: next(versions),
        process_runner=runner,
    )

    assert result.outcome is UpdateTransactionOutcome.COMPLETED
    assert "--require-registered-plugin" in calls[1]
    assert captured_specs[0].require_registered_plugin is True
    from autoskillit.workspace import InstallStateLeaseMode

    assert captured_specs[0].lease_mode is InstallStateLeaseMode.SHARED
    assert captured_specs[0].expected_version == "1.1.0"
    assert result.expected_version == "1.1.0"
    assert result.verified_identity == f"{_PLUGIN_REF}:1.1.0"
    assert lease.closed is True
    assert result.install_result is not None
    assert result.install_result.outcome is InstallOutcome.COMPLETED
    assert result.phase_history == UPDATE_TRANSACTION_PHASES
    assert result.irreversible_pivot_crossed is True


def test_pre_update_obligation_is_immutable_but_post_update_evidence_is_fresh(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _prepare(monkeypatch)
    registry = _register_plugin(tmp_path)
    versions = iter(["1.0.0", "1.1.0"])
    calls: list[list[str]] = []
    finding = SimpleNamespace(
        severity=Severity.ERROR,
        check="installed_plugin_registry_missing",
        message="fresh registry has no exact current publication",
    )

    def runner(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[Any]:
        calls.append(list(cmd))
        if len(calls) == 1:
            registry.unlink()
        return subprocess.CompletedProcess(cmd, 0)

    def verify(_spec: Any) -> Any:
        assert not registry.exists()
        return SimpleNamespace(identity=None, findings=(finding,), lease=None)

    monkeypatch.setattr(
        "autoskillit.cli.update._transaction.verify_installed_plugin_artifact",
        verify,
    )
    result = run_update_transaction(
        home=tmp_path,
        base_env={
            "PATH": "/bin",
            "AUTOSKILLIT_AGENT_BACKEND__BACKEND": "codex",
        },
        version_reader=lambda _name: next(versions),
        process_runner=runner,
    )

    assert "--require-registered-plugin" in calls[1]
    assert result.outcome is UpdateTransactionOutcome.FAILED_POSTCONDITION
    assert any("fresh registry" in item for item in result.findings)
    assert result.phase_history == UPDATE_TRANSACTION_PHASES
    assert result.irreversible_pivot_crossed is True


@pytest.mark.parametrize(
    ("artifact_state", "check", "message", "has_identity"),
    [
        (
            "absent-root",
            "installed_plugin_root_missing",
            "exact current-version root is absent",
            False,
        ),
        (
            "dangling-registry",
            "installed_plugin_registry_dangling",
            "registered path is dangling",
            False,
        ),
        (
            "corrupt-identity",
            "installed_plugin_identity_malformed",
            "identity sidecar is malformed",
            False,
        ),
        (
            "wrong-identity",
            "installed_plugin_identity_mismatch",
            "semantic key or incarnation is wrong",
            True,
        ),
        (
            "digest-mismatch",
            "installed_plugin_digest_mismatch",
            "published content digest does not match",
            True,
        ),
    ],
)
def test_required_invalid_artifact_states_fail_only_at_final_verification(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    artifact_state: str,
    check: str,
    message: str,
    has_identity: bool,
) -> None:
    _prepare(monkeypatch)
    _register_plugin(tmp_path)
    versions = iter(["1.0.0", "1.1.0"])
    calls: list[list[str]] = []
    finding = SimpleNamespace(
        severity=Severity.ERROR,
        check=check,
        message=message,
    )
    identity = SimpleNamespace(semantic_key=f"{_PLUGIN_REF}:1.1.0") if has_identity else None
    monkeypatch.setattr(
        "autoskillit.cli.update._transaction.verify_installed_plugin_artifact",
        lambda _spec: SimpleNamespace(
            identity=identity,
            findings=(finding,),
            lease=None,
        ),
    )

    result = run_update_transaction(
        home=tmp_path,
        base_env={"PATH": "/bin"},
        version_reader=lambda _name: next(versions),
        process_runner=_recording_success_runner(calls),
    )

    assert len(calls) == 2, artifact_state
    assert result.outcome is UpdateTransactionOutcome.FAILED_POSTCONDITION
    assert any(message in item for item in result.findings)
    assert result.phase_history == UPDATE_TRANSACTION_PHASES
    assert result.irreversible_pivot_crossed is True


def test_verification_error_is_failed_postcondition(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _prepare(monkeypatch)
    _register_plugin(tmp_path)
    versions = iter(["1.0.0", "1.1.0"])
    finding = SimpleNamespace(
        severity=Severity.ERROR,
        check="installed_plugin_registry_missing",
        message="missing exact registration",
    )
    monkeypatch.setattr(
        "autoskillit.cli.update._transaction.verify_installed_plugin_artifact",
        lambda _spec: SimpleNamespace(identity=None, findings=(finding,), lease=None),
    )

    result = run_update_transaction(
        home=tmp_path,
        base_env={"PATH": "/bin"},
        version_reader=lambda _name: next(versions),
        process_runner=lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, 0),
    )

    assert result.outcome is UpdateTransactionOutcome.FAILED_POSTCONDITION
    assert result.install_result is not None
    assert any("missing exact registration" in item for item in result.findings)
    assert result.phase_history == UPDATE_TRANSACTION_PHASES
    assert result.irreversible_pivot_crossed is True


def test_git_contained_maintenance_cwd_fails_before_upgrade_and_is_removed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _prepare(monkeypatch)
    monkeypatch.setattr(
        "autoskillit.cli.update._transaction.is_git_main_checkout",
        lambda _path: True,
    )
    calls: list[list[str]] = []

    result = run_update_transaction(
        home=tmp_path,
        base_env={"PATH": "/bin"},
        version_reader=lambda _name: "1.0.0",
        process_runner=_recording_success_runner(calls),
    )

    assert result.outcome is UpdateTransactionOutcome.FAILED_UPGRADE
    assert not calls
    assert not list((tmp_path / ".autoskillit").glob("update-maintenance-*"))
    _assert_terminal_history(
        result,
        UpdateTransactionPhase.MAINTENANCE_CONTEXT_CONSTRUCTION,
    )
    assert result.irreversible_pivot_crossed is False


def _isolated_child_environment(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    home = tmp_path / "home"
    fake_bin = tmp_path / "bin"
    xdg_config = tmp_path / "xdg-config"
    xdg_cache = tmp_path / "xdg-cache"
    xdg_data = tmp_path / "xdg-data"
    xdg_state = tmp_path / "xdg-state"
    xdg_runtime = tmp_path / "xdg-runtime"
    for directory in (
        home,
        fake_bin,
        xdg_config,
        xdg_cache,
        xdg_data,
        xdg_state,
        xdg_runtime,
    ):
        directory.mkdir()

    installed_version = importlib.metadata.version("autoskillit")
    fake_claude = fake_bin / "claude"
    fake_claude.write_text(
        f"""#!{sys.executable}
import json
import os
import signal
import sys
from pathlib import Path

home = Path(os.environ["HOME"])
behavior_path = home / "fake-claude-behavior"
behavior = (
    behavior_path.read_text(encoding="utf-8").strip()
    if behavior_path.exists()
    else "success"
)
argv = sys.argv[1:]
with (home / "fake-claude-calls.jsonl").open("a", encoding="utf-8") as trace:
    trace.write(json.dumps({{
        "argv": argv,
        "cwd": str(Path.cwd()),
        "home": os.environ["HOME"],
        "xdg_config_home": os.environ.get("XDG_CONFIG_HOME"),
        "claudecode": os.environ.get("CLAUDECODE"),
        "backend": os.environ.get("AUTOSKILLIT_AGENT_BACKEND__BACKEND"),
    }}) + "\\n")

if behavior == "signal" and argv[:3] == ["plugin", "marketplace", "add"]:
    os.kill(os.getppid(), signal.SIGTERM)
    raise SystemExit(0)
if argv[:2] != ["plugin", "install"]:
    raise SystemExit(0)
if behavior == "child-failure":
    raise SystemExit(9)
if behavior == "missing-artifact":
    raise SystemExit(0)

version = {installed_version!r}
root = home / ".claude" / "plugins" / "cache" / "autoskillit-local" / "autoskillit" / version
metadata = root / ".claude-plugin" / "plugin.json"
hooks = root / "hooks" / "hooks.json"
metadata.parent.mkdir(parents=True, exist_ok=True)
hooks.parent.mkdir(parents=True, exist_ok=True)
metadata.write_text(
    json.dumps({{"name": "autoskillit", "version": version}}),
    encoding="utf-8",
)
hooks.write_text(json.dumps({{"hooks": {{}}}}), encoding="utf-8")
registry = home / ".claude" / "plugins" / "installed_plugins.json"
registry.parent.mkdir(parents=True, exist_ok=True)
registry.write_text(
    json.dumps({{
        "version": 2,
        "plugins": {{
            "autoskillit@autoskillit-local": [{{
                "installPath": str(root),
                "scope": "user",
            }}],
        }},
    }}),
    encoding="utf-8",
)
""",
        encoding="utf-8",
    )
    fake_claude.chmod(0o755)

    autoskillit_entrypoint = fake_bin / "autoskillit"
    child_bootstrap = (
        "import runpy,sys;"
        "import autoskillit.cli._marketplace as marketplace;"
        "marketplace.is_git_worktree=lambda _path:False;"
        "sys.argv[0]='autoskillit';"
        "runpy.run_module('autoskillit',run_name='__main__')"
    )
    autoskillit_entrypoint.write_text(
        (
            "#!/bin/sh\n"
            'if [ -f "$HOME/fake-install-unknown-status" ]; then exit 99; fi\n'
            f'exec {shlex.quote(sys.executable)} -c {shlex.quote(child_bootstrap)} "$@"\n'
        ),
        encoding="utf-8",
    )
    autoskillit_entrypoint.chmod(0o755)
    env = {
        "HOME": str(home),
        "PATH": str(fake_bin),
        "XDG_CONFIG_HOME": str(xdg_config),
        "XDG_CACHE_HOME": str(xdg_cache),
        "XDG_DATA_HOME": str(xdg_data),
        "XDG_STATE_HOME": str(xdg_state),
        "XDG_RUNTIME_DIR": str(xdg_runtime),
        "USER": "autoskillit-test",
        "LOGNAME": "autoskillit-test",
        "AUTOSKILLIT_AGENT_BACKEND__BACKEND": "codex",
    }
    return home, env


def _seed_pre_update_installed_mode(home: Path) -> None:
    build_plugin_artifact_state(
        home,
        PluginArtifactStateKind.VALID_CURRENT,
        expected_version="0.0.0",
    )


def _read_fake_claude_calls(home: Path) -> list[dict[str, Any]]:
    trace = home / "fake-claude-calls.jsonl"
    if not trace.exists():
        return []
    return [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines() if line]


def test_coordinator_runs_real_install_adapter_with_exact_isolated_context(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _prepare(monkeypatch)
    home, base_env = _isolated_child_environment(tmp_path)
    installed_version = importlib.metadata.version("autoskillit")
    versions = iter(["0.0.0", installed_version])
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def runner(
        cmd: list[str],
        **kwargs: Any,
    ) -> subprocess.CompletedProcess[Any]:
        calls.append((list(cmd), kwargs))
        assert set(kwargs) == {"check", "env", "cwd"}
        assert kwargs["check"] is False
        if len(calls) == 1:
            return subprocess.CompletedProcess(cmd, 0)
        return subprocess.run(cmd, **kwargs)

    result = run_update_transaction(
        home=home,
        base_env=base_env,
        version_reader=lambda _name: next(versions),
        process_runner=runner,
    )

    assert result.outcome is UpdateTransactionOutcome.COMPLETED
    assert result.install_result is not None
    assert result.install_result.outcome is InstallOutcome.NOT_REQUIRED
    assert len(calls) == 2
    upgrade_kwargs = calls[0][1]
    install_kwargs = calls[1][1]
    assert upgrade_kwargs["env"] is install_kwargs["env"]
    assert upgrade_kwargs["cwd"] == install_kwargs["cwd"]
    maintenance_env = install_kwargs["env"]
    assert maintenance_env["HOME"] == str(home)
    assert maintenance_env["XDG_CONFIG_HOME"] == base_env["XDG_CONFIG_HOME"]
    assert maintenance_env["XDG_CACHE_HOME"] == base_env["XDG_CACHE_HOME"]
    assert maintenance_env["PATH"] == base_env["PATH"]
    assert "AUTOSKILLIT_AGENT_BACKEND__BACKEND" not in maintenance_env
    assert not Path(install_kwargs["cwd"]).exists()
    assert not (home / "fake-claude-calls.jsonl").exists()
    assert result.phase_history == UPDATE_TRANSACTION_PHASES


@pytest.mark.parametrize(
    (
        "claude_behavior",
        "expected_status",
        "expected_outcome",
        "expected_install_outcome",
        "expected_failure_kind",
    ),
    [
        (
            "success",
            int(InstallProcessStatus.SUCCESS),
            UpdateTransactionOutcome.COMPLETED,
            InstallOutcome.COMPLETED,
            None,
        ),
        (
            "child-failure",
            int(InstallProcessStatus.FAILED_CHILD),
            UpdateTransactionOutcome.FAILED_INSTALL,
            InstallOutcome.FAILED,
            InstallFailureKind.CHILD,
        ),
        (
            "missing-artifact",
            int(InstallProcessStatus.FAILED_POSTCONDITION),
            UpdateTransactionOutcome.FAILED_POSTCONDITION,
            InstallOutcome.FAILED,
            InstallFailureKind.POSTCONDITION,
        ),
    ],
)
def test_registered_plugin_crosses_real_install_cli_with_typed_statuses(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    claude_behavior: str,
    expected_status: int,
    expected_outcome: UpdateTransactionOutcome,
    expected_install_outcome: InstallOutcome,
    expected_failure_kind: InstallFailureKind | None,
) -> None:
    _prepare(monkeypatch)
    home, base_env = _isolated_child_environment(tmp_path)
    _seed_pre_update_installed_mode(home)
    (home / "fake-claude-behavior").write_text(claude_behavior, encoding="utf-8")
    installed_version = importlib.metadata.version("autoskillit")
    versions = iter(["0.0.0", installed_version])
    calls: list[tuple[list[str], dict[str, Any]]] = []
    install_statuses: list[int] = []

    def runner(
        cmd: list[str],
        **kwargs: Any,
    ) -> subprocess.CompletedProcess[Any]:
        calls.append((list(cmd), kwargs))
        assert set(kwargs) == {"check", "env", "cwd"}
        assert kwargs["check"] is False
        if len(calls) == 1:
            return subprocess.CompletedProcess(cmd, 0)
        completed = subprocess.run(cmd, **kwargs)
        install_statuses.append(completed.returncode)
        return completed

    result = run_update_transaction(
        home=home,
        base_env=base_env,
        version_reader=lambda _name: next(versions),
        process_runner=runner,
    )

    assert result.outcome is expected_outcome
    assert result.install_result is not None
    assert result.install_result.outcome is expected_install_outcome
    assert result.install_result.failure_kind is expected_failure_kind
    assert install_statuses == [expected_status]
    assert "--require-registered-plugin" in calls[1][0]
    assert calls[0][1]["env"] is calls[1][1]["env"]
    assert calls[0][1]["cwd"] == calls[1][1]["cwd"]
    claude_calls = _read_fake_claude_calls(home)
    assert [call["argv"][:2] for call in claude_calls] == [
        ["plugin", "marketplace"],
        ["plugin", "install"],
    ]
    assert all(call["cwd"] == str(calls[1][1]["cwd"]) for call in claude_calls)
    assert all(call["home"] == str(home) for call in claude_calls)
    assert all(call["xdg_config_home"] == base_env["XDG_CONFIG_HOME"] for call in claude_calls)
    assert all(call["claudecode"] is None for call in claude_calls)
    assert all(call["backend"] is None for call in claude_calls)
    if expected_outcome is UpdateTransactionOutcome.COMPLETED:
        assert result.phase_history == UPDATE_TRANSACTION_PHASES
        assert result.verified_identity == f"{_PLUGIN_REF}:{installed_version}"
    else:
        _assert_terminal_history(
            result,
            UpdateTransactionPhase.INSTALL_STATUS_RECONSTRUCTION,
        )
        assert UpdateTransactionPhase.POST_UPDATE_ARTIFACT_VERIFICATION not in result.phase_history
    assert not Path(calls[1][1]["cwd"]).exists()


@pytest.mark.parametrize(
    (
        "child_behavior",
        "expected_process_status",
        "expected_outcome",
        "expected_install_outcome",
        "expected_failure_kind",
    ),
    [
        (
            "launch-failure",
            None,
            UpdateTransactionOutcome.FAILED_INSTALL,
            InstallOutcome.FAILED,
            InstallFailureKind.CHILD,
        ),
        (
            "signal",
            -15,
            UpdateTransactionOutcome.INDETERMINATE,
            InstallOutcome.INDETERMINATE,
            None,
        ),
        (
            "unknown-status",
            99,
            UpdateTransactionOutcome.INDETERMINATE,
            InstallOutcome.INDETERMINATE,
            None,
        ),
    ],
)
def test_real_install_process_launch_signal_and_unknown_statuses_stop_verification(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    child_behavior: str,
    expected_process_status: int | None,
    expected_outcome: UpdateTransactionOutcome,
    expected_install_outcome: InstallOutcome,
    expected_failure_kind: InstallFailureKind | None,
) -> None:
    _prepare(monkeypatch)
    home, base_env = _isolated_child_environment(tmp_path)
    _seed_pre_update_installed_mode(home)
    if child_behavior == "signal":
        (home / "fake-claude-behavior").write_text("signal", encoding="utf-8")
    elif child_behavior == "unknown-status":
        (home / "fake-install-unknown-status").touch()
    installed_version = importlib.metadata.version("autoskillit")
    versions = iter(["0.0.0", installed_version])
    calls: list[tuple[list[str], dict[str, Any]]] = []
    install_statuses: list[int] = []

    def runner(
        cmd: list[str],
        **kwargs: Any,
    ) -> subprocess.CompletedProcess[Any]:
        calls.append((list(cmd), kwargs))
        assert set(kwargs) == {"check", "env", "cwd"}
        assert kwargs["check"] is False
        if len(calls) == 1:
            return subprocess.CompletedProcess(cmd, 0)
        if child_behavior == "launch-failure":
            (tmp_path / "bin" / "autoskillit").unlink()
        completed = subprocess.run(cmd, **kwargs)
        install_statuses.append(completed.returncode)
        return completed

    monkeypatch.setattr(
        "autoskillit.cli.update._transaction.verify_installed_plugin_artifact",
        lambda _spec: pytest.fail("non-success child status reached verification"),
    )
    result = run_update_transaction(
        home=home,
        base_env=base_env,
        version_reader=lambda _name: next(versions),
        process_runner=runner,
    )

    assert result.outcome is expected_outcome
    assert result.install_result is not None
    assert result.install_result.outcome is expected_install_outcome
    assert result.install_result.failure_kind is expected_failure_kind
    assert install_statuses == (
        [] if expected_process_status is None else [expected_process_status]
    )
    assert "--require-registered-plugin" in calls[1][0]
    assert calls[0][1]["env"] is calls[1][1]["env"]
    assert calls[0][1]["cwd"] == calls[1][1]["cwd"]
    _assert_terminal_history(
        result,
        UpdateTransactionPhase.INSTALL_STATUS_RECONSTRUCTION,
    )
    assert UpdateTransactionPhase.POST_UPDATE_ARTIFACT_VERIFICATION not in result.phase_history
    assert not Path(calls[1][1]["cwd"]).exists()
    if child_behavior == "signal":
        assert [call["argv"][:3] for call in _read_fake_claude_calls(home)] == [
            ["plugin", "marketplace", "add"]
        ]
    else:
        assert not _read_fake_claude_calls(home)


@pytest.mark.parametrize("consumer", ["explicit", "automatic"])
@pytest.mark.parametrize("claude_behavior", ["success", "child-failure"])
def test_update_consumers_compose_with_registered_real_child_transaction(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    consumer: str,
    claude_behavior: str,
) -> None:
    from autoskillit.cli.update import _update, _update_checks

    _prepare(monkeypatch)
    home, base_env = _isolated_child_environment(tmp_path)
    _seed_pre_update_installed_mode(home)
    (home / "fake-claude-behavior").write_text(claude_behavior, encoding="utf-8")
    installed_version = importlib.metadata.version("autoskillit")
    versions = iter(["0.0.0", installed_version])
    calls: list[tuple[list[str], dict[str, Any]]] = []
    results: list[Any] = []
    effects: list[tuple[str, Path, dict[str, object] | None]] = []
    dismiss_reads: list[Path] = []
    install_statuses: list[int] = []

    def runner(
        cmd: list[str],
        **kwargs: Any,
    ) -> subprocess.CompletedProcess[Any]:
        calls.append((list(cmd), kwargs))
        assert set(kwargs) == {"check", "env", "cwd"}
        if len(calls) == 1:
            return subprocess.CompletedProcess(cmd, 0)
        completed = subprocess.run(cmd, **kwargs)
        install_statuses.append(completed.returncode)
        return completed

    def configured_transaction(
        *,
        home: Path,
        process_runner: Callable[..., subprocess.CompletedProcess[Any]],
    ) -> Any:
        assert home == Path(base_env["HOME"])
        assert process_runner is subprocess.run
        result = run_update_transaction(
            home=home,
            base_env=base_env,
            version_reader=lambda _name: next(versions),
            process_runner=runner,
        )
        results.append(result)
        return result

    initial_state: dict[str, object] = {
        "update_prompt": {"dismissed_version": "0.0.0"},
        "binary_snoozed": True,
    }

    def read_dismiss_state(target_home: Path) -> dict[str, object]:
        dismiss_reads.append(target_home)
        return dict(initial_state)

    monkeypatch.setattr(
        _update_checks,
        "_read_dismiss_state",
        read_dismiss_state,
    )
    monkeypatch.setattr(
        _update_checks,
        "_write_dismiss_state",
        lambda target_home, state: effects.append(("write", target_home, dict(state))),
    )
    monkeypatch.setattr(
        _update_checks,
        "invalidate_fetch_cache",
        lambda target_home: effects.append(("invalidate", target_home, None)),
    )

    if consumer == "explicit":
        monkeypatch.setattr(_update, "run_update_transaction", configured_transaction)
        monkeypatch.setattr(
            _update,
            "perform_restart",
            lambda: effects.append(("restart", home, None)),
        )
        if claude_behavior == "success":
            _update.run_update_command(home=home)
        else:
            with pytest.raises(SystemExit) as exc_info:
                _update.run_update_command(home=home)
            assert exc_info.value.code == int(UpdateProcessStatus.FAILED_INSTALL)
    else:
        monkeypatch.setattr(
            _update_checks,
            "run_update_transaction",
            configured_transaction,
        )
        monkeypatch.setattr(
            _update_checks,
            "perform_restart",
            lambda: effects.append(("restart", home, None)),
        )
        automatic_state = dict(initial_state)
        assert (
            _update_checks._run_update_sequence(
                home,
                automatic_state,
            )
            is None
        )
        assert automatic_state == ({} if claude_behavior == "success" else initial_state)

    assert len(results) == 1
    assert results[0].install_result is not None
    assert "--require-registered-plugin" in calls[1][0]
    assert install_statuses == [
        int(
            InstallProcessStatus.SUCCESS
            if claude_behavior == "success"
            else InstallProcessStatus.FAILED_CHILD
        )
    ]
    if claude_behavior == "success":
        assert results[0].outcome is UpdateTransactionOutcome.COMPLETED
        assert results[0].install_result.outcome is InstallOutcome.COMPLETED
        assert [effect[0] for effect in effects] == ["write", "invalidate", "restart"]
        assert all(effect[1] == home for effect in effects)
        assert effects[0][2] == {}
        assert dismiss_reads == ([home] if consumer == "explicit" else [])
    else:
        assert results[0].outcome is UpdateTransactionOutcome.FAILED_INSTALL
        assert results[0].install_result.outcome is InstallOutcome.FAILED
        assert results[0].install_result.failure_kind is InstallFailureKind.CHILD
        assert not effects
        assert not dismiss_reads
    assert [call["argv"][:2] for call in _read_fake_claude_calls(home)] == [
        ["plugin", "marketplace"],
        ["plugin", "install"],
    ]
    assert not Path(calls[1][1]["cwd"]).exists()
