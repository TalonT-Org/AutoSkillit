"""Declared environment channels consumed directly by hook processes."""

from __future__ import annotations

from ._hooks_defs import HookEnvVarDef


def _validate_hook_env_contract(entries: tuple[HookEnvVarDef, ...]) -> None:
    allowed_provenance = {"autoskillit", "harness", "operator"}
    variables = [entry.var for entry in entries]
    if len(variables) != len(set(variables)):
        raise AssertionError("HOOK_ENV_CONTRACT contains duplicate variables")
    for entry in entries:
        if entry.provenance not in allowed_provenance:
            raise AssertionError(f"HOOK_ENV_CONTRACT has unknown provenance for {entry.var!r}")
        if entry.provenance == "autoskillit" and (not entry.producer or not entry.entrypoint):
            raise AssertionError(
                f"AutoSkillit-owned env var {entry.var!r} requires producer and entrypoint"
            )
        if entry.provenance != "autoskillit" and (
            entry.producer is not None or entry.entrypoint is not None
        ):
            raise AssertionError(
                f"Externally-owned env var {entry.var!r} must not declare producer or entrypoint"
            )


HOOK_ENV_CONTRACT: tuple[HookEnvVarDef, ...] = (
    HookEnvVarDef(
        "AUTOSKILLIT_AGENT_BACKEND",
        "autoskillit",
        "autoskillit.execution.backends.claude:ClaudeCodeBackend.build_skill_session_cmd",
        "autoskillit.execution.backends.claude:ClaudeCodeBackend.build_skill_session_cmd",
        "Backend builders identify the authoritative child backend for hook policy.",
    ),
    HookEnvVarDef(
        "AUTOSKILLIT_ALLOWED_WRITE_PREFIX",
        "autoskillit",
        "autoskillit.execution.backends._backend_cmd_builder_base:BackendCmdBuilderBase._assemble_shared_env_extras",
        "autoskillit.execution.backends.claude:ClaudeCodeBackend.build_skill_session_cmd",
        "The shared backend builder delivers the singular write-scope boundary.",
    ),
    HookEnvVarDef(
        "AUTOSKILLIT_ALLOWED_WRITE_PREFIXES",
        "autoskillit",
        "autoskillit.execution.backends._backend_cmd_builder_base:BackendCmdBuilderBase._assemble_shared_env_extras",
        "autoskillit.execution.backends.claude:ClaudeCodeBackend.build_skill_session_cmd",
        "The shared backend builder delivers the complete write-scope boundary list.",
    ),
    HookEnvVarDef(
        "AUTOSKILLIT_APPLICABLE_GUARDS",
        "autoskillit",
        "autoskillit.execution.backends._backend_cmd_builder_base:BackendCmdBuilderBase._assemble_shared_env_extras",
        "autoskillit.execution.backends.claude:ClaudeCodeBackend.build_skill_session_cmd",
        "The shared backend builder selects the guards applicable to the child.",
    ),
    HookEnvVarDef(
        "AUTOSKILLIT_CAMPAIGN_ID",
        "autoskillit",
        "autoskillit.fleet.dispatch._api:_run_dispatch",
        "autoskillit.fleet.dispatch._api:execute_dispatch",
        "Fleet dispatch binds child hook state to the owning campaign identity.",
    ),
    HookEnvVarDef(
        "AUTOSKILLIT_COMPLETION_MARKER",
        "autoskillit",
        "autoskillit.execution.backends.claude:ClaudeCodeBackend.build_skill_session_cmd",
        "autoskillit.execution.backends.claude:ClaudeCodeBackend.build_skill_session_cmd",
        "Skill-session builders bind completion output to the expected marker.",
    ),
    HookEnvVarDef(
        "AUTOSKILLIT_CWD",
        "autoskillit",
        "autoskillit.execution.backends._backend_cmd_builder_base:BackendCmdBuilderBase._assemble_shared_env_extras",
        "autoskillit.execution.backends.claude:ClaudeCodeBackend.build_skill_session_cmd",
        "The shared backend builder supplies the authoritative project working root.",
    ),
    HookEnvVarDef(
        "AUTOSKILLIT_DISPATCH_ID",
        "autoskillit",
        "autoskillit.fleet.dispatch._api:_run_dispatch",
        "autoskillit.fleet.dispatch._api:execute_dispatch",
        "Fleet dispatch binds hook diagnostics and policy to the current dispatch.",
    ),
    HookEnvVarDef(
        "AUTOSKILLIT_HEADLESS",
        "autoskillit",
        "autoskillit.execution.backends._backend_cmd_builder_base:BackendCmdBuilderBase._assemble_shared_env_extras",
        "autoskillit.execution.backends.claude:ClaudeCodeBackend.build_skill_session_cmd",
        "The shared backend builder marks automated child sessions for hook gates.",
    ),
    HookEnvVarDef(
        "AUTOSKILLIT_LAUNCH_ID",
        "autoskillit",
        "autoskillit.cli.session._session_cook:cook",
        "autoskillit.cli.session._session_cook:cook",
        "Interactive cook startup binds hook state to the managed launch identity.",
    ),
    HookEnvVarDef(
        "AUTOSKILLIT_LOG_DIR",
        "operator",
        None,
        None,
        "Operators may redirect bounded hook diagnostics to an explicit log root.",
    ),
    HookEnvVarDef(
        "AUTOSKILLIT_MANAGED_ATTEMPT_ID",
        "autoskillit",
        "autoskillit.execution.backends._backend_cmd_builder_base:_managed_native_shell_env",
        "autoskillit.execution.backends.codex:CodexBackend.build_skill_session_cmd",
        "The Codex builder binds managed shell output to one execution attempt.",
    ),
    HookEnvVarDef(
        "AUTOSKILLIT_MANAGED_LAUNCH_ID",
        "autoskillit",
        "autoskillit.execution.backends._backend_cmd_builder_base:_managed_native_shell_env",
        "autoskillit.execution.backends.codex:CodexBackend.build_skill_session_cmd",
        "The Codex builder binds managed shell output to one launch identity.",
    ),
    HookEnvVarDef(
        "AUTOSKILLIT_MANAGED_LINEAGE_DIGEST",
        "autoskillit",
        "autoskillit.execution.backends._backend_cmd_builder_base:_managed_native_shell_env",
        "autoskillit.execution.backends.codex:CodexBackend.build_skill_session_cmd",
        "The Codex builder signs the lineage delivered to the shell-capture hook.",
    ),
    HookEnvVarDef(
        "AUTOSKILLIT_MANAGED_LINEAGE_REF",
        "autoskillit",
        "autoskillit.execution.backends._backend_cmd_builder_base:_managed_native_shell_env",
        "autoskillit.execution.backends.codex:CodexBackend.build_skill_session_cmd",
        "The Codex builder serializes the lineage delivered to shell capture.",
    ),
    HookEnvVarDef(
        "AUTOSKILLIT_NATIVE_SHELL_CAPTURE_MODE",
        "autoskillit",
        "autoskillit.execution.backends._backend_cmd_builder_base:_managed_native_shell_env",
        "autoskillit.execution.backends.codex:CodexBackend.build_skill_session_cmd",
        "The Codex builder declares whether native shell capture is managed.",
    ),
    HookEnvVarDef(
        "AUTOSKILLIT_PROTECTED_BRANCHES",
        "operator",
        None,
        None,
        "Operators may extend the protected-branch policy beyond repository defaults.",
    ),
    HookEnvVarDef(
        "AUTOSKILLIT_PROVIDER_PROFILE",
        "autoskillit",
        "autoskillit.cli.session._session_cook:cook",
        "autoskillit.cli.session._session_cook:cook",
        "Cook startup selects the provider profile inherited by hook policy.",
    ),
    HookEnvVarDef(
        "AUTOSKILLIT_QUOTA_GUARD__BUFFER_SECONDS",
        "operator",
        None,
        None,
        "Operators may override the quota safety buffer for a local session.",
    ),
    HookEnvVarDef(
        "AUTOSKILLIT_QUOTA_GUARD__CACHE_MAX_AGE",
        "operator",
        None,
        None,
        "Operators may override acceptable quota-cache freshness for local policy.",
    ),
    HookEnvVarDef(
        "AUTOSKILLIT_QUOTA_GUARD__CACHE_PATH",
        "operator",
        None,
        None,
        "Operators may point quota policy at an explicitly managed cache artifact.",
    ),
    HookEnvVarDef(
        "AUTOSKILLIT_QUOTA_GUARD__DISABLED",
        "operator",
        None,
        None,
        "Operators may deliberately disable quota enforcement for a local session.",
    ),
    HookEnvVarDef(
        "AUTOSKILLIT_SESSION_DEADLINE",
        "autoskillit",
        "autoskillit.fleet.dispatch._api:_run_dispatch",
        "autoskillit.fleet.dispatch._api:execute_dispatch",
        "Fleet dispatch propagates the bounded child-session execution deadline.",
    ),
    HookEnvVarDef(
        "AUTOSKILLIT_SESSION_TYPE",
        "autoskillit",
        "autoskillit.cli.session._session_cook:cook",
        "autoskillit.cli.session._session_cook:cook",
        "Cook startup identifies the orchestration tier consumed by hook gates.",
    ),
    HookEnvVarDef(
        "AUTOSKILLIT_SKILL_NAME",
        "autoskillit",
        "autoskillit.execution.backends.claude:ClaudeCodeBackend.build_skill_session_cmd",
        "autoskillit.execution.backends.claude:ClaudeCodeBackend.build_skill_session_cmd",
        "Skill-session builders identify the loaded skill to hook policy.",
    ),
    HookEnvVarDef(
        "AUTOSKILLIT_STATE_DIR",
        "operator",
        None,
        None,
        "Operators and isolated harnesses may override the kitchen marker directory.",
    ),
    HookEnvVarDef(
        "AUTOSKILLIT_STATE_ROOT",
        "autoskillit",
        "autoskillit.cli.session._session_launch:_run_interactive_session",
        "autoskillit.cli.session._session_launch:_run_interactive_session",
        "Interactive launch binds all hook state to the orchestrating project root.",
    ),
    HookEnvVarDef(
        "AUTOSKILLIT_WRITE_GUARD_TOOL_NAMES",
        "autoskillit",
        "autoskillit.execution.backends._backend_cmd_builder_base:BackendCmdBuilderBase._assemble_shared_env_extras",
        "autoskillit.execution.backends.claude:ClaudeCodeBackend.build_skill_session_cmd",
        "The shared backend builder selects tools governed by write-scope policy.",
    ),
    HookEnvVarDef(
        "DBUS_SESSION_BUS_ADDRESS",
        "operator",
        None,
        None,
        "The host session bus address is supplied by the operating environment.",
    ),
    HookEnvVarDef(
        "XDG_DATA_HOME",
        "operator",
        None,
        None,
        "The operating environment may select the platform-standard data root.",
    ),
)

_validate_hook_env_contract(HOOK_ENV_CONTRACT)
