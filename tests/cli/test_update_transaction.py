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
    InstallOutcome,
    InstallProcessStatus,
)
from autoskillit.cli._install_info import InstallInfo, InstallType
from autoskillit.cli.update._transaction import (
    IRREVERSIBLE_PIVOT_PHASE,
    UPDATE_TRANSACTION_PHASES,
    UpdateTransactionOutcome,
    UpdateTransactionPhase,
    run_update_transaction,
)
from autoskillit.core import _AUTOSKILLIT_PLUGIN_KEY as _PLUGIN_REF
from tests.cli._self_invoke_helpers import assert_valid_maintenance_install_argv

pytestmark = [pytest.mark.layer("cli"), pytest.mark.medium]


def test_post_pivot_reporter_falls_back_when_logger_raises(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from autoskillit.cli.update import _transaction as transaction_module

    def fail_logger(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("logger unavailable")

    monkeypatch.setattr(transaction_module.logger, "warning", fail_logger)

    try:
        raise RuntimeError("original failure")
    except RuntimeError:
        transaction_module._report_post_pivot_failure("post-pivot operation failed")

    assert capsys.readouterr().err == "post-pivot operation failed\n"


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


def _stub_generation_verification(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the post-update generation-store verification succeed with a fake identity."""
    import autoskillit.core as core

    fake_gen = Path("/tmp/fake-generation")

    monkeypatch.setattr(
        core,
        "resolve_current_generation",
        lambda _home, _ref, _version: fake_gen,
    )

    def _fake_read_identity(*_args: object, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(
            semantic_key=f"{_PLUGIN_REF}:{importlib.metadata.version('autoskillit')}",
            incarnation_id="0" * 32,
        )

    monkeypatch.setattr(
        core,
        "read_installed_plugin_artifact_identity",
        _fake_read_identity,
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
    _stub_generation_verification(monkeypatch)


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
        assert_valid_maintenance_install_argv(cmd)
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0)

    return runner


# ---------------------------------------------------------------------------
# Pivot-safe update verification and failure reporting.
# ---------------------------------------------------------------------------

# Purge imported Rich modules before blocking imports so eager and lazy
# dependency versions exercise the same missing-site-packages behavior.
_POISON_RICH_PREFIX = """\
import sys

for _name in list(sys.modules):
    if _name == "rich" or _name.startswith("rich."):
        del sys.modules[_name]


class _RichBlocker:
    def find_spec(self, name, path=None, target=None):
        if name == "rich" or name.startswith("rich."):
            raise ModuleNotFoundError(f"simulated deleted site-packages tree: {name}")
        return None


sys.meta_path.insert(0, _RichBlocker())
"""


def _run_poisoned_subprocess(driver_body: str) -> subprocess.CompletedProcess[str]:
    """Run ``driver_body`` in a fresh subprocess with rich purged and blocked.

    ``driver_body`` should print "SURVIVED" on success — the shared success
    marker every caller asserts on.
    """
    script = _POISON_RICH_PREFIX + "\n" + driver_body
    return subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=20,
    )


def test_raising_post_pivot_probe_survives_real_logging_chain() -> None:
    """A raising post-pivot probe remains reportable without Rich imports."""
    driver = """
import importlib.metadata
import subprocess
import tempfile
from pathlib import Path

import autoskillit.cli.update._transaction as t
from autoskillit.cli._install_info import InstallInfo, InstallType


def _info():
    return InstallInfo(InstallType.GIT_VCS, "abc123", "stable", "https://x", None)


t.detect_install = _info
t.upgrade_command = lambda _info: ["uv", "tool", "upgrade", "autoskillit"]
t.is_git_worktree = lambda _p: False
t.is_git_main_checkout = lambda _p: False


def raising_prober(info, maintenance_env, runner):
    raise importlib.metadata.PackageNotFoundError("autoskillit")


def runner(cmd, **kwargs):
    return subprocess.CompletedProcess(cmd, 0)


with tempfile.TemporaryDirectory() as tmp:
    result = t.run_update_transaction(
        home=Path(tmp),
        base_env={"PATH": "/bin"},
        version_reader=lambda _name: "1.0.0",
        fresh_version_prober=raising_prober,
        process_runner=runner,
    )
    assert result.outcome.value == "failed-upgrade", result.outcome
    assert result.findings, "expected findings on failure"

print("SURVIVED", flush=True)
"""
    result = _run_poisoned_subprocess(driver)
    assert result.returncode == 0, (
        f"process crashed instead of reporting the mapped failure.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "SURVIVED" in result.stdout
    assert "ModuleNotFoundError" not in result.stderr


def test_module_default_logging_survives_deleted_rich_tree() -> None:
    """Default logging renders exceptions when Rich is unavailable."""
    driver = """
from autoskillit.core.logging import get_logger

logger = get_logger("t_b2_default")
try:
    raise RuntimeError("simulated post-pivot failure")
except Exception:
    logger.warning("simulated_failure", exc_info=True)

print("SURVIVED", flush=True)
"""
    result = _run_poisoned_subprocess(driver)
    assert result.returncode == 0, (
        f"process crashed rendering the exception.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "SURVIVED" in result.stdout
    assert "ModuleNotFoundError" not in result.stderr
    # The module default's WriteLoggerFactory writes to sys.stderr — assert
    # the log line actually reached the stream, not just that nothing raised.
    assert "simulated_failure" in result.stderr


def test_configure_logging_console_branch_survives_deleted_rich_tree() -> None:
    """Configured console logging renders exceptions without Rich."""
    driver = """
from autoskillit.core import configure_logging, get_logger


class _FakeTTY:
    def __init__(self):
        self.buf = []

    def write(self, s):
        self.buf.append(s)
        return len(s)

    def flush(self):
        pass

    def isatty(self):
        return True


stream = _FakeTTY()
configure_logging(json_output=False, stream=stream)
logger = get_logger("t_b2_configured")
try:
    raise RuntimeError("simulated post-pivot failure")
except Exception:
    logger.warning("simulated_failure", exc_info=True)

print("SURVIVED", flush=True)
print("STREAM_CONTENT_START", flush=True)
print("".join(stream.buf), flush=True)
print("STREAM_CONTENT_END", flush=True)
"""
    result = _run_poisoned_subprocess(driver)
    assert result.returncode == 0, (
        f"process crashed rendering the exception.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "SURVIVED" in result.stdout
    assert "ModuleNotFoundError" not in result.stderr
    # configure_logging()'s console branch writes to the injected `stream`
    # (never sys.stderr directly) — assert the log line actually reached it,
    # not just that nothing raised.
    stream_content = result.stdout.split("STREAM_CONTENT_START\n", 1)[1].split(
        "STREAM_CONTENT_END\n", 1
    )[0]
    assert "simulated_failure" in stream_content


def test_verifier_fault_survives_real_logging_chain() -> None:
    """Artifact-verification faults remain reportable without Rich."""
    driver = """
import json
import subprocess
import tempfile
from pathlib import Path

import autoskillit.cli.update._transaction as t
from autoskillit.cli._install_info import InstallInfo, InstallType


def _info():
    return InstallInfo(InstallType.GIT_VCS, "abc123", "stable", "https://x", None)


t.detect_install = _info
t.upgrade_command = lambda _info: ["uv", "tool", "upgrade", "autoskillit"]
t.is_git_worktree = lambda _p: False
t.is_git_main_checkout = lambda _p: False


def raising_resolve_current_generation(home, plugin_ref, version):
    raise RuntimeError("simulated verification crash (deleted tree)")


import autoskillit.core as core_module

core_module.resolve_current_generation = raising_resolve_current_generation


def runner(cmd, **kwargs):
    return subprocess.CompletedProcess(cmd, 0)


with tempfile.TemporaryDirectory() as tmp:
    tmp_path = Path(tmp)
    registry = tmp_path / ".claude" / "plugins" / "installed_plugins.json"
    registry.parent.mkdir(parents=True)
    registry.write_text(json.dumps({
        "version": 2,
        "plugins": {"autoskillit@autoskillit-local": [{"installPath": str(tmp_path / "x")}]},
    }))
    result = t.run_update_transaction(
        home=tmp_path,
        base_env={"PATH": "/bin"},
        version_reader=lambda _name: "1.0.0",
        fresh_version_prober=lambda _i, _e, _r: "1.1.0",
        process_runner=runner,
    )
    assert result.outcome.value == "failed-postcondition", result.outcome
    assert result.findings, "expected findings on failure"

print("SURVIVED", flush=True)
"""
    result = _run_poisoned_subprocess(driver)
    assert result.returncode == 0, (
        f"process crashed instead of reporting the mapped failure.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "SURVIVED" in result.stdout
    assert "ModuleNotFoundError" not in result.stderr


def test_post_pivot_verification_is_out_of_process(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Post-pivot verification uses the fresh subprocess probe."""
    _prepare(monkeypatch)
    call_count = 0

    def counting_version_reader(_name: str) -> str:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return "1.0.0"
        pytest.fail("post-pivot read must go through fresh_version_prober, not version_reader")

    calls: list[list[str]] = []
    result = run_update_transaction(
        home=tmp_path,
        base_env={"PATH": "/bin"},
        version_reader=counting_version_reader,
        fresh_version_prober=lambda _info, _env, _runner: "1.1.0",
        process_runner=_recording_success_runner(calls),
    )

    assert result.outcome is UpdateTransactionOutcome.COMPLETED
    assert result.expected_version == "1.1.0"
    assert len(calls) == 2
    assert call_count == 1
    assert result.phase_history == UPDATE_TRANSACTION_PHASES


def test_default_fresh_version_prober_uses_resolved_autoskillit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The default fresh probe invokes the resolved CLI in the maintenance env."""
    fake_entrypoint = tmp_path / "bin" / "autoskillit"
    fake_entrypoint.parent.mkdir()
    fake_entrypoint.write_text("#!/bin/sh\necho 9.9.9\n")
    fake_entrypoint.chmod(0o755)
    monkeypatch.setattr(
        "autoskillit.cli.update._transaction.detect_install",
        lambda: InstallInfo(
            InstallType.GIT_VCS,
            "abc",
            "stable",
            "https://x",
            None,
            entrypoint=fake_entrypoint,
        ),
    )
    monkeypatch.setattr(
        "autoskillit.cli.update._transaction.upgrade_command",
        lambda _info: ["uv", "tool", "upgrade", "autoskillit"],
    )
    monkeypatch.setattr("autoskillit.cli.update._transaction.is_git_worktree", lambda _path: False)
    monkeypatch.setattr(
        "autoskillit.cli.update._transaction.is_git_main_checkout", lambda _path: False
    )
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def runner(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[Any]:
        assert_valid_maintenance_install_argv(cmd)
        calls.append((list(cmd), kwargs))
        if len(calls) == 2:
            return subprocess.run(cmd, **kwargs)
        return subprocess.CompletedProcess(cmd, 0)

    run_update_transaction(
        home=tmp_path,
        base_env={"PATH": "/bin"},
        process_runner=runner,
    )

    probe_cmd, probe_kwargs = calls[1]
    assert probe_cmd == [str(fake_entrypoint), "--version"]
    assert probe_kwargs["capture_output"] is True
    assert probe_kwargs["text"] is True
    assert probe_kwargs["env"]["PATH"] == "/bin"


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
        assert_valid_maintenance_install_argv(cmd)
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
        fresh_version_prober=lambda _info, _env, _runner: "1.0.0",
        process_runner=_recording_success_runner(calls),
    )

    assert result.outcome is UpdateTransactionOutcome.FAILED_UPGRADE
    assert len(calls) == 1
    _assert_terminal_history(result, UpdateTransactionPhase.FRESH_VERSION_METADATA_GATE)
    assert result.irreversible_pivot_crossed is True


def test_unregistered_success_preserves_preexisting_obligation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from autoskillit.workspace import read_obligation, write_obligation

    _prepare(monkeypatch)
    existing = write_obligation(
        tmp_path,
        previous_version="0.9.0",
        originating_phase="earlier-registered-update",
    )

    def runner(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[Any]:
        return subprocess.CompletedProcess(cmd, 0)

    result = run_update_transaction(
        home=tmp_path,
        base_env={"PATH": "/bin"},
        version_reader=lambda _name: "1.0.0",
        fresh_version_prober=lambda _info, _env, _runner: "1.1.0",
        process_runner=runner,
    )

    assert result.outcome is UpdateTransactionOutcome.COMPLETED
    assert read_obligation(tmp_path) == existing


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
        fresh_version_prober=lambda _info, _env, _runner: next(versions),
        process_runner=lambda cmd, **kwargs: (
            assert_valid_maintenance_install_argv(cmd),
            subprocess.CompletedProcess(cmd, next(statuses)),
        )[1],
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
        "autoskillit.core.resolve_current_generation",
        lambda *_a, **_kw: pytest.fail("no prior registration must not invent an obligation"),
    )
    versions = iter(["1.0.0", "1.1.0"])
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def runner(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[Any]:
        assert_valid_maintenance_install_argv(cmd)
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
        fresh_version_prober=lambda _info, _env, _runner: next(versions),
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
        "autoskillit.core.resolve_current_generation",
        lambda *_a, **_kw: pytest.fail("no prior registration must not invent an obligation"),
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
        assert_valid_maintenance_install_argv(cmd)
        calls.append((list(cmd), kwargs))
        return subprocess.CompletedProcess(cmd, 0)

    with structlog.testing.capture_logs() as logs:
        result = run_update_transaction(
            home=home,
            base_env={**approved_base_env, **sensitive_env},
            version_reader=lambda _name: next(versions),
            fresh_version_prober=lambda _info, _env, _runner: next(versions),
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

    def runner(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[Any]:
        assert_valid_maintenance_install_argv(cmd)
        calls.append(list(cmd))
        if len(calls) == 2:
            _register_plugin(tmp_path, "1.1.0")
        return subprocess.CompletedProcess(cmd, 0)

    captured_generation_lookups: list[tuple[Any, ...]] = []
    gen_root = tmp_path / "generation-root"
    gen_root.mkdir()

    def resolve_current_generation(home: Path, plugin_ref: str, version: str) -> Path | None:
        captured_generation_lookups.append((home, plugin_ref, version))
        registry_state = json.loads(registry.read_text(encoding="utf-8"))
        fresh_path = registry_state["plugins"][_PLUGIN_REF][0]["installPath"]
        assert fresh_path.endswith("/1.1.0")
        return gen_root

    def read_installed_plugin_artifact_identity(managed_path: Path, **_kwargs: Any) -> Any:
        assert managed_path == gen_root
        assert _kwargs["expected_semantic_key"] == f"{_PLUGIN_REF}:1.1.0"
        return SimpleNamespace(semantic_key=f"{_PLUGIN_REF}:1.1.0")

    monkeypatch.setattr(
        "autoskillit.core.resolve_current_generation",
        resolve_current_generation,
    )
    monkeypatch.setattr(
        "autoskillit.core.read_installed_plugin_artifact_identity",
        read_installed_plugin_artifact_identity,
    )
    result = run_update_transaction(
        home=tmp_path,
        base_env={
            "PATH": "/bin",
            "AUTOSKILLIT_AGENT_BACKEND__BACKEND": "codex",
        },
        version_reader=lambda _name: next(versions),
        fresh_version_prober=lambda _info, _env, _runner: next(versions),
        process_runner=runner,
    )

    assert result.outcome is UpdateTransactionOutcome.COMPLETED
    assert "--require-registered-plugin" in calls[1]
    assert captured_generation_lookups == [
        (tmp_path.expanduser().absolute(), _PLUGIN_REF, "1.1.0")
    ]
    assert result.expected_version == "1.1.0"
    assert result.verified_identity == f"{_PLUGIN_REF}:1.1.0"
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

    def runner(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[Any]:
        assert_valid_maintenance_install_argv(cmd)
        calls.append(list(cmd))
        if len(calls) == 1:
            registry.unlink()
        return subprocess.CompletedProcess(cmd, 0)

    registry_state_at_verification: list[bool] = []

    def resolve_current_generation(*_a: Any, **_kw: Any) -> Path | None:
        registry_state_at_verification.append(registry.exists())
        return None

    monkeypatch.setattr(
        "autoskillit.core.resolve_current_generation",
        resolve_current_generation,
    )
    result = run_update_transaction(
        home=tmp_path,
        base_env={
            "PATH": "/bin",
            "AUTOSKILLIT_AGENT_BACKEND__BACKEND": "codex",
        },
        version_reader=lambda _name: next(versions),
        fresh_version_prober=lambda _info, _env, _runner: next(versions),
        process_runner=runner,
    )

    assert "--require-registered-plugin" in calls[1]
    # The upgrade subprocess deleted the pre-update registry snapshot; by the
    # time verification runs (post-install), that mutation is already
    # visible — confirming verification consults fresh, not cached, state.
    assert registry_state_at_verification == [False]
    assert result.outcome is UpdateTransactionOutcome.FAILED_POSTCONDITION
    assert any("No current generation found after install" in item for item in result.findings)
    assert result.phase_history == UPDATE_TRANSACTION_PHASES
    assert result.irreversible_pivot_crossed is True


@pytest.mark.parametrize(
    ("artifact_state", "expected_message"),
    [
        ("absent-generation", "No current generation found after install"),
        ("wrong-identity", "Installed plugin verification failed"),
        ("digest-mismatch", "Installed plugin verification failed"),
    ],
)
def test_required_invalid_artifact_states_fail_only_at_final_verification(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    artifact_state: str,
    expected_message: str,
) -> None:
    _prepare(monkeypatch)
    _register_plugin(tmp_path)
    versions = iter(["1.0.0", "1.1.0"])
    calls: list[list[str]] = []

    if artifact_state == "absent-generation":
        monkeypatch.setattr(
            "autoskillit.core.resolve_current_generation",
            lambda *_a, **_kw: None,
        )
    else:
        gen_root = tmp_path / "generation-root"
        gen_root.mkdir()
        monkeypatch.setattr(
            "autoskillit.core.resolve_current_generation",
            lambda *_a, **_kw: gen_root,
        )

        from autoskillit.core import PluginArtifactValidationError

        validation_message = {
            "wrong-identity": "installed plugin semantic identity mismatch",
            "digest-mismatch": "installed plugin content digest mismatch",
        }[artifact_state]

        def raising_read_identity(*_a: Any, **_kw: Any) -> Any:
            raise PluginArtifactValidationError(validation_message)

        monkeypatch.setattr(
            "autoskillit.core.read_installed_plugin_artifact_identity",
            raising_read_identity,
        )

    result = run_update_transaction(
        home=tmp_path,
        base_env={"PATH": "/bin"},
        version_reader=lambda _name: next(versions),
        fresh_version_prober=lambda _info, _env, _runner: next(versions),
        process_runner=_recording_success_runner(calls),
    )

    assert len(calls) == 2, artifact_state
    assert result.outcome is UpdateTransactionOutcome.FAILED_POSTCONDITION
    assert any(expected_message in item for item in result.findings)
    assert result.phase_history == UPDATE_TRANSACTION_PHASES
    assert result.irreversible_pivot_crossed is True


def test_verification_error_is_failed_postcondition(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _prepare(monkeypatch)
    _register_plugin(tmp_path)
    versions = iter(["1.0.0", "1.1.0"])
    monkeypatch.setattr(
        "autoskillit.core.resolve_current_generation",
        lambda *_a, **_kw: None,
    )

    result = run_update_transaction(
        home=tmp_path,
        base_env={"PATH": "/bin"},
        version_reader=lambda _name: next(versions),
        fresh_version_prober=lambda _info, _env, _runner: next(versions),
        process_runner=lambda cmd, **kwargs: (
            assert_valid_maintenance_install_argv(cmd),
            subprocess.CompletedProcess(cmd, 0),
        )[1],
    )

    assert result.outcome is UpdateTransactionOutcome.FAILED_POSTCONDITION
    assert result.install_result is not None
    assert any("No current generation found after install" in item for item in result.findings)
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
        fresh_version_prober=lambda _info, _env, _runner: next(versions),
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


# ---------------------------------------------------------------------------
# Publication-obligation journal lifecycle.
# ---------------------------------------------------------------------------


def test_obligation_written_before_upgrade_launch_and_cleared_on_completion(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A successful transaction writes the obligation immediately before the
    upgrade subprocess launches (observed via an injected runner that reads
    the journal from inside the upgrade call itself) and clears it by
    RESULT_FINALIZATION.
    """
    from autoskillit.workspace import read_obligation

    _prepare(monkeypatch)
    _register_plugin(tmp_path)
    gen_root = tmp_path / "generation-root"
    gen_root.mkdir()
    monkeypatch.setattr(
        "autoskillit.core.resolve_current_generation",
        lambda *_a, **_kw: gen_root,
    )
    monkeypatch.setattr(
        "autoskillit.core.read_installed_plugin_artifact_identity",
        lambda *_a, **_kw: SimpleNamespace(semantic_key=f"{_PLUGIN_REF}:1.1.0"),
    )
    versions = iter(["1.0.0", "1.1.0"])
    obligation_present_at_upgrade_launch: list[bool] = []

    def runner(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[Any]:
        if cmd[:3] == ["uv", "tool", "upgrade"]:
            obligation_present_at_upgrade_launch.append(read_obligation(tmp_path) is not None)
        return subprocess.CompletedProcess(cmd, 0)

    result = run_update_transaction(
        home=tmp_path,
        base_env={"PATH": "/bin"},
        version_reader=lambda _name: next(versions),
        fresh_version_prober=lambda _info, _env, _runner: next(versions),
        process_runner=runner,
    )

    assert obligation_present_at_upgrade_launch == [True]
    assert result.outcome is UpdateTransactionOutcome.COMPLETED
    assert read_obligation(tmp_path) is None


@pytest.mark.parametrize(
    ("failure_point", "expect_expected_version"),
    [
        ("upgrade_nonzero_exit", False),
        ("uv_oserror", False),
        ("raising_probe", False),
        ("child_failure_after_probe", True),
        ("child_oserror", True),
        ("verifier_raises_after_probe", True),
    ],
)
def test_obligation_survives_failures_at_or_after_upgrade_subprocess(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure_point: str,
    expect_expected_version: bool,
) -> None:
    """For each fault-injected failure at or after the upgrade subprocess,
    the obligation survives — with expected_version backfilled for failures
    AFTER the probe succeeded, and None for failures at/before it (the new
    version was never established, and the record must say so rather than
    guess).
    """
    from autoskillit.hook_registry import generate_hooks_json, validate_plugin_cache_hooks
    from autoskillit.workspace import read_obligation

    _prepare(monkeypatch)
    _register_plugin(tmp_path)
    hooks_dir = (
        tmp_path
        / ".claude"
        / "plugins"
        / "cache"
        / "autoskillit-local"
        / "autoskillit"
        / "1.0.0"
        / "hooks"
    )
    hooks_dir.mkdir(parents=True)
    (hooks_dir / "_dispatch.py").write_text("# dispatcher stub", encoding="utf-8")
    (hooks_dir / "hooks.json").write_text(
        json.dumps(generate_hooks_json()),
        encoding="utf-8",
    )
    if failure_point == "verifier_raises_after_probe":
        monkeypatch.setattr(
            "autoskillit.core.resolve_current_generation",
            lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("simulated verify crash")),
        )

    calls: list[list[str]] = []

    def runner(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[Any]:
        assert_valid_maintenance_install_argv(cmd)
        calls.append(list(cmd))
        if failure_point == "upgrade_nonzero_exit" and len(calls) == 1:
            return subprocess.CompletedProcess(cmd, 7)
        if failure_point == "uv_oserror" and len(calls) == 1:
            raise OSError("simulated uv-install launch failure")
        if failure_point == "child_failure_after_probe" and len(calls) == 2:
            return subprocess.CompletedProcess(cmd, 9)
        if failure_point == "child_oserror" and len(calls) == 2:
            raise OSError("simulated child install launch failure")
        return subprocess.CompletedProcess(cmd, 0)

    prober = (
        (lambda _i, _e, _r: (_ for _ in ()).throw(RuntimeError("simulated probe failure")))
        if failure_point == "raising_probe"
        else (lambda _i, _e, _r: "1.1.0")
    )

    result = run_update_transaction(
        home=tmp_path,
        base_env={"PATH": "/bin"},
        version_reader=lambda _name: "1.0.0",
        fresh_version_prober=prober,
        process_runner=runner,
    )

    assert result.outcome is not UpdateTransactionOutcome.COMPLETED, failure_point
    obligation = read_obligation(tmp_path)
    assert obligation is not None, failure_point
    if expect_expected_version:
        assert obligation.expected_version == "1.1.0", failure_point
    else:
        assert obligation.expected_version is None, failure_point
    assert validate_plugin_cache_hooks(cache_dir=hooks_dir.parent.parent) == [], failure_point


@pytest.mark.parametrize(
    "failure_point",
    [
        "unknown_install_type",
        "claudecode_deferral",
        "maintenance_context_failure",
        "worktree_refusal",
    ],
)
def test_no_obligation_for_failures_before_upgrade_subprocess(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failure_point: str
) -> None:
    """Every failure/deferral strictly before the upgrade subprocess launches
    mutates nothing and must leave NO obligation — a pending obligation here
    would trigger spurious repairs forever.
    """
    from autoskillit.workspace import read_obligation

    _prepare(monkeypatch)
    _register_plugin(tmp_path)
    base_env: dict[str, str] = {"PATH": "/bin"}

    if failure_point == "unknown_install_type":
        monkeypatch.setattr(
            "autoskillit.cli.update._transaction.upgrade_command",
            lambda _info: None,
        )
    elif failure_point == "claudecode_deferral":
        base_env["CLAUDECODE"] = "1"
    elif failure_point == "maintenance_context_failure":
        monkeypatch.setattr(
            "autoskillit.cli.update._transaction.build_maintenance_env",
            lambda *_a, **_kw: (_ for _ in ()).throw(ValueError("simulated env build failure")),
        )
    elif failure_point == "worktree_refusal":
        monkeypatch.setattr(
            "autoskillit.cli.update._transaction.is_git_main_checkout",
            lambda _path: True,
        )

    result = run_update_transaction(
        home=tmp_path,
        base_env=base_env,
        version_reader=lambda _name: "1.0.0",
        process_runner=_recording_success_runner([]),
    )

    assert result.outcome is not UpdateTransactionOutcome.COMPLETED, failure_point
    assert read_obligation(tmp_path) is None, failure_point


def test_failing_obligation_write_aborts_before_upgrade_subprocess(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A failed obligation write aborts the transaction with a mapped
    failure BEFORE the uv subprocess launches — nothing is yet mutated, so
    refusing to proceed is safe, and it upholds the invariant that the
    irreversible region is entered only with the breadcrumb already on disk.
    """
    _prepare(monkeypatch)
    _register_plugin(tmp_path)
    monkeypatch.setattr(
        "autoskillit.cli.update._transaction.write_obligation",
        lambda *_a, **_kw: (_ for _ in ()).throw(OSError("simulated disk full")),
    )
    calls: list[list[str]] = []

    result = run_update_transaction(
        home=tmp_path,
        base_env={"PATH": "/bin"},
        version_reader=lambda _name: "1.0.0",
        process_runner=_recording_success_runner(calls),
    )

    assert result.outcome is UpdateTransactionOutcome.FAILED_UPGRADE
    assert not calls, "uv subprocess must never launch when the obligation write fails"
