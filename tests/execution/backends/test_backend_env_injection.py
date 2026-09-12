"""Parametrized assertions: both builders inject AUTOSKILLIT_WRITE_GUARD_TOOL_NAMES."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core import (
    AUDIT_ADMISSION_AUTHORITY_PATH_ENV_VAR,
    AUTOSKILLIT_STATE_ROOT_ENV_VAR,
    CHILD_OUTCOME_LOG_DIR_ENV_VAR,
    CmdSpec,
)
from autoskillit.execution.backends.claude import ClaudeCodeBackend
from autoskillit.execution.backends.codex import CodexBackend
from autoskillit.execution.otlp_sink import _build_env
from tests.execution.backends._plugin_binding import plugin_binding

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(AUDIT_ADMISSION_AUTHORITY_PATH_ENV_VAR, raising=False)
    monkeypatch.delenv("AUTOSKILLIT_CAMPAIGN_ID", raising=False)
    monkeypatch.delenv("AUTOSKILLIT_KITCHEN_SESSION_ID", raising=False)
    monkeypatch.delenv("AUTOSKILLIT_AGENT_BACKEND", raising=False)
    monkeypatch.delenv("AUTOSKILLIT_AGENT_BACKEND__BACKEND", raising=False)


def test_skill_session_cmd_injects_write_guard_tool_names() -> None:
    from autoskillit.execution.backends import BACKEND_REGISTRY

    assert BACKEND_REGISTRY, "BACKEND_REGISTRY is empty"
    for name, cls in BACKEND_REGISTRY.items():
        backend = cls()
        spec = backend.build_skill_session_cmd(
            "/autoskillit:investigate", "/repo", completion_marker="DONE"
        )
        assert "AUTOSKILLIT_WRITE_GUARD_TOOL_NAMES" in spec.env, (
            f"{name}: AUTOSKILLIT_WRITE_GUARD_TOOL_NAMES missing from build_skill_session_cmd env"
        )
        assert spec.env["AUTOSKILLIT_WRITE_GUARD_TOOL_NAMES"], (
            f"{name}: AUTOSKILLIT_WRITE_GUARD_TOOL_NAMES is empty in build_skill_session_cmd env"
        )


def test_food_truck_cmd_injects_write_guard_tool_names() -> None:
    from autoskillit.execution.backends import BACKEND_REGISTRY

    assert BACKEND_REGISTRY, "BACKEND_REGISTRY is empty"
    for name, cls in BACKEND_REGISTRY.items():
        backend = cls()
        spec = backend.build_food_truck_cmd(
            orchestrator_prompt="test prompt",
            plugin_binding=plugin_binding(Path("/plugins")),
            cwd="/repo",
            completion_marker="DONE",
        )
        assert "AUTOSKILLIT_WRITE_GUARD_TOOL_NAMES" in spec.env, (
            f"{name}: AUTOSKILLIT_WRITE_GUARD_TOOL_NAMES missing from build_food_truck_cmd env"
        )
        assert spec.env["AUTOSKILLIT_WRITE_GUARD_TOOL_NAMES"], (
            f"{name}: AUTOSKILLIT_WRITE_GUARD_TOOL_NAMES is empty in build_food_truck_cmd env"
        )


@pytest.mark.parametrize(
    ("backend_factory", "absent_value"),
    [
        pytest.param(ClaudeCodeBackend, None, id="claude-omits"),
        pytest.param(CodexBackend, "", id="codex-empty-sentinel"),
    ],
)
def test_skill_session_audit_authority_env_contract(
    backend_factory: type[ClaudeCodeBackend] | type[CodexBackend],
    absent_value: str | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trusted_path = "/parent/.autoskillit/temp/audit-admission/ledger.sqlite3"
    monkeypatch.setenv(
        AUDIT_ADMISSION_AUTHORITY_PATH_ENV_VAR,
        "/untrusted/ambient/audit-admission.sqlite3",
    )
    backend = backend_factory()

    attested = backend.build_skill_session_cmd(
        "/autoskillit:investigate",
        "/clone",
        completion_marker="DONE",
        provider_extras={AUDIT_ADMISSION_AUTHORITY_PATH_ENV_VAR: trusted_path},
    )
    non_attested = backend.build_skill_session_cmd(
        "/autoskillit:investigate", "/clone", completion_marker="DONE"
    )

    assert attested.env[AUDIT_ADMISSION_AUTHORITY_PATH_ENV_VAR] == trusted_path
    assert attested.cwd == "/clone"
    assert attested.env["AUTOSKILLIT_CWD"] == "/clone"
    assert attested.env[AUTOSKILLIT_STATE_ROOT_ENV_VAR] == "/clone"
    assert non_attested.cwd == "/clone"
    assert non_attested.env["AUTOSKILLIT_CWD"] == "/clone"
    assert non_attested.env[AUTOSKILLIT_STATE_ROOT_ENV_VAR] == "/clone"
    if absent_value is None:
        assert AUDIT_ADMISSION_AUTHORITY_PATH_ENV_VAR not in non_attested.env
    else:
        assert non_attested.env[AUDIT_ADMISSION_AUTHORITY_PATH_ENV_VAR] == absent_value


@pytest.mark.parametrize(
    "backend_factory",
    [
        pytest.param(ClaudeCodeBackend, id="claude"),
        pytest.param(CodexBackend, id="codex"),
    ],
)
def test_skill_session_cmd_injects_child_outcome_log_dir(
    backend_factory: type[ClaudeCodeBackend] | type[CodexBackend],
) -> None:
    """The resolved child-terminal-reason snapshot root (issue #4623) reaches the

    launched command's own env only when a non-empty value is supplied — the
    var is otherwise omitted entirely, mirroring the AUTOSKILLIT_CWD case.
    """
    backend = backend_factory()

    carrying = backend.build_skill_session_cmd(
        "/autoskillit:investigate",
        "/clone",
        completion_marker="DONE",
        child_outcome_log_dir="/diag-root",
    )
    assert carrying.env[CHILD_OUTCOME_LOG_DIR_ENV_VAR] == "/diag-root"

    omitted = backend.build_skill_session_cmd(
        "/autoskillit:investigate", "/clone", completion_marker="DONE"
    )
    assert CHILD_OUTCOME_LOG_DIR_ENV_VAR not in omitted.env


@pytest.mark.parametrize(
    "backend_factory",
    [
        pytest.param(ClaudeCodeBackend, id="claude"),
        pytest.param(CodexBackend, id="codex"),
    ],
)
def test_interactive_cmd_never_carries_child_outcome_log_dir(
    backend_factory: type[ClaudeCodeBackend] | type[CodexBackend],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An interactive cook launch never inherits AUTOSKILLIT_CHILD_OUTCOME_LOG_DIR.

    Simulates a headless-launched process (which received the var on its own
    env) spawning an interactive cook session as its own child: the ambient
    value must be scrubbed, not forwarded — interactive hooks resolve their
    own operator/default root instead (issue #4623 design decision).
    """
    monkeypatch.setenv(CHILD_OUTCOME_LOG_DIR_ENV_VAR, "/leaked-headless-root")
    backend = backend_factory()

    spec = backend.build_interactive_cmd()

    assert CHILD_OUTCOME_LOG_DIR_ENV_VAR not in spec.env


def test_native_otlp_activation_uses_each_backends_supported_launch_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify Claude uses sink env while Codex emits run-scoped CLI overrides."""
    sink_env = _build_env("http://127.0.0.1:4318")
    for key in (
        "CLAUDE_CODE_ENABLE_TELEMETRY",
        "OTEL_LOGS_EXPORTER",
        "OTEL_METRICS_EXPORTER",
        "OTEL_METRICS_INCLUDE_SESSION_ID",
    ):
        monkeypatch.setenv(key, "hostile-ambient")

    claude = ClaudeCodeBackend().build_skill_session_cmd(
        "/autoskillit:investigate",
        "/clone",
        completion_marker="DONE",
        provider_extras=sink_env,
    )
    codex = CodexBackend().build_skill_session_cmd(
        "/autoskillit:investigate",
        "/clone",
        completion_marker="DONE",
        provider_extras=sink_env,
    )

    for key in (
        "CLAUDE_CODE_ENABLE_TELEMETRY",
        "OTEL_LOGS_EXPORTER",
        "OTEL_METRICS_EXPORTER",
        "OTEL_METRICS_INCLUDE_SESSION_ID",
    ):
        assert claude.env[key] == sink_env[key]
        assert key not in codex.env
    assert any(value.startswith("otel.exporter=") for value in codex.cmd)
    assert any(value.startswith("otel.metrics_exporter=") for value in codex.cmd)


@pytest.mark.parametrize(
    "builder_name",
    ("food-truck", "headless", "resume", "interactive"),
)
def test_all_codex_builders_forward_audit_authority_or_empty_sentinel(
    builder_name: str,
) -> None:
    backend = CodexBackend()
    trusted_path = "/parent/.autoskillit/temp/audit-admission/ledger.sqlite3"

    def build(env_extras: dict[str, str] | None) -> CmdSpec:
        if builder_name == "food-truck":
            return backend.build_food_truck_cmd(
                orchestrator_prompt="test prompt",
                plugin_binding=None,
                cwd="/clone",
                completion_marker="DONE",
                env_extras=env_extras,
            )
        if builder_name == "headless":
            return backend.build_headless_cmd("test prompt", env_extras=env_extras)
        if builder_name == "resume":
            return backend.build_resume_cmd(
                resume_session_id="session-id",
                prompt="test prompt",
                env_extras=env_extras,
            )
        assert builder_name == "interactive"
        return backend.build_interactive_cmd(env_extras=env_extras)

    attested = build({AUDIT_ADMISSION_AUTHORITY_PATH_ENV_VAR: trusted_path})
    non_attested = build(None)

    assert attested.env[AUDIT_ADMISSION_AUTHORITY_PATH_ENV_VAR] == trusted_path
    assert AUDIT_ADMISSION_AUTHORITY_PATH_ENV_VAR in non_attested.env
    assert non_attested.env[AUDIT_ADMISSION_AUTHORITY_PATH_ENV_VAR] == ""
