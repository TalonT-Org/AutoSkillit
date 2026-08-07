"""Canonical location for the BackendCmdBuilderBase ABC and shared env-assembly keys.

Both ``ClaudeCodeBackend`` (in ``claude.py``) and ``CodexBackend`` (in ``codex.py``)
inherit from :class:`BackendCmdBuilderBase`. The base class owns the shared
``_assemble_shared_env_extras`` static helper and four abstract extension points
(``_binary``, ``_sandbox_default``, ``_env_policy``, ``_flag_vocabulary``) that
each concrete backend implements.

This module is stdlib-only plus IL-0 core imports. It is IL-1 compliant — no
imports from ``claude.py`` or ``codex.py`` (the two concrete backends).
"""

from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, NamedTuple

from autoskillit.core import (
    AUTOSKILLIT_APPLICABLE_GUARDS,
    AUTOSKILLIT_STATE_ROOT_ENV_VAR,
    AUTOSKILLIT_WRITE_GUARD_TOOL_NAMES,
    CAMPAIGN_ID_ENV_VAR,
    CODEX_COOK_RESERVED_ENV_VARS,
    CODEX_STARTUP_TRACE_ENV_VAR,
    KITCHEN_SESSION_ID_ENV_VAR,
    MANAGED_ATTEMPT_ID_ENV_VAR,
    MANAGED_LAUNCH_ID_ENV_VAR,
    MANAGED_LINEAGE_DIGEST_ENV_VAR,
    MANAGED_LINEAGE_REF_ENV_VAR,
    NATIVE_SHELL_CAPTURE_MODE_ENV_VAR,
    ExecutionIdentity,
    ManagedHeadlessSessionLineageRef,
    NativeShellCaptureDecision,
    SkillSessionConfig,
)

if TYPE_CHECKING:
    from autoskillit.core import EnvPolicy


# Injected into every AutoSkillit-launched headless and cook session.
# Raises the Claude Code client-side MCP tool result inline token limit from
# 25,000 to 50,000. NOTE: This does NOT control the ~100KB disk-persistence
# gate — persistence is governed by a separate byte-size threshold in the
# Claude Code harness (empirically ~100KB on CLI 2.1.197). See issue #4253.
_MAX_MCP_OUTPUT_TOKENS_VALUE: str = "50000"


# Baseline env vars injected into EVERY AutoSkillit-launched session. Callers
# can override via env_extras. The shim ``_codex_exec_extras`` (in
# ``codex.py``) imports this directly for the ``include_session_baseline=True``
# path so resume sessions do NOT inadvertently pick up ambient campaign/kitchen
# IDs from ``os.environ`` (which ``_assemble_shared_env_extras`` would).
SHARED_BASELINE_ENV: Mapping[str, str] = MappingProxyType(
    {
        "MAX_MCP_OUTPUT_TOKENS": _MAX_MCP_OUTPUT_TOKENS_VALUE,
        "MCP_CONNECTION_NONBLOCKING": "0",
    }
)

_PROTECTED_NATIVE_SHELL_ENV_VARS: frozenset[str] = frozenset(
    {
        NATIVE_SHELL_CAPTURE_MODE_ENV_VAR,
        MANAGED_LAUNCH_ID_ENV_VAR,
        MANAGED_ATTEMPT_ID_ENV_VAR,
        MANAGED_LINEAGE_DIGEST_ENV_VAR,
        MANAGED_LINEAGE_REF_ENV_VAR,
    }
)


def _filter_protected_native_shell_env(extras: Mapping[str, str]) -> dict[str, str]:
    """Remove controls that only managed Codex builders may author."""
    return {
        key: value for key, value in extras.items() if key not in _PROTECTED_NATIVE_SHELL_ENV_VARS
    }


def _merge_caller_env_extras(
    target: dict[str, str],
    extras: Mapping[str, str] | None,
    *,
    denylist: frozenset[str] = frozenset(),
) -> None:
    """Merge caller extras without admitting Codex cook-owned controls."""
    if extras is not None:
        blocked = denylist | CODEX_COOK_RESERVED_ENV_VARS | {CODEX_STARTUP_TRACE_ENV_VAR}
        filtered_extras = _filter_protected_native_shell_env(extras)
        target.update({key: value for key, value in filtered_extras.items() if key not in blocked})


def _managed_native_shell_env(
    *,
    decision: NativeShellCaptureDecision | None,
    lineage_ref: ManagedHeadlessSessionLineageRef | None,
    attempt_id: str | None,
) -> dict[str, str]:
    """Serialize the complete trusted child control or reject partial identity."""
    values = (decision, lineage_ref, attempt_id)
    if all(value is None for value in values):
        return {}
    if not all(value is not None for value in values):
        raise ValueError("managed native shell capture fields must be supplied together")
    assert decision is not None
    assert lineage_ref is not None
    assert attempt_id is not None
    if len(attempt_id) != 32 or any(char not in "0123456789abcdef" for char in attempt_id):
        raise ValueError("managed_attempt_id must be 32 lowercase hexadecimal characters")
    return {
        NATIVE_SHELL_CAPTURE_MODE_ENV_VAR: decision.mode.value,
        MANAGED_LAUNCH_ID_ENV_VAR: lineage_ref.launch_id,
        MANAGED_ATTEMPT_ID_ENV_VAR: attempt_id,
        MANAGED_LINEAGE_DIGEST_ENV_VAR: lineage_ref.lineage_digest,
        MANAGED_LINEAGE_REF_ENV_VAR: json.dumps(
            lineage_ref.to_dict(),
            sort_keys=True,
            separators=(",", ":"),
        ),
    }


class FlagVocabulary(NamedTuple):
    """Per-backend CLI flag metadata used by the shared ``CmdBuilder``."""

    variadic_flags: frozenset[str]
    non_variadic_flags: frozenset[str]
    model_flag: str
    add_dir_flag: str
    resume_flag: str
    config_override_flag: str


@dataclass(frozen=True, slots=True)
class BackendCmdBuilderBase(ABC):
    """Base class for backend command builders.

    Owns the shared env-assembly logic that both ``ClaudeCodeBackend`` and
    ``CodexBackend`` previously duplicated. Subclasses must implement four
    abstract extension points that capture backend-specific behavior.
    """

    @abstractmethod
    def _binary(self) -> str:
        """Return the CLI binary name for this backend."""

    @abstractmethod
    def _sandbox_default(self) -> str:
        """Return the default sandbox mode string for this backend."""

    @abstractmethod
    def _env_policy(self) -> EnvPolicy:
        """Return the backend's :class:`EnvPolicy` instance."""

    @abstractmethod
    def _flag_vocabulary(self) -> FlagVocabulary:
        """Return the backend's :class:`FlagVocabulary`."""

    def resolve_effective_execution_identity(
        self,
        *,
        requested: ExecutionIdentity,
        session_id: str,
    ) -> ExecutionIdentity:
        """Return requested identity when the backend has no effective evidence source."""
        del session_id
        return requested

    @staticmethod
    def _assemble_shared_env_extras(
        *,
        session_type: str = "",
        applicable_guards: frozenset[str] = frozenset(),
        write_guard_tool_names: frozenset[str] = frozenset(),
        write_prefix: str = "",
        write_prefixes: tuple[str, ...] = (),
        cwd: str = "",
        scenario_step_name: str = "",
    ) -> dict[str, str]:
        """Assemble the shared env keys consumed by both backends.

        Always-on keys (three): ``MAX_MCP_OUTPUT_TOKENS``, ``MCP_CONNECTION_NONBLOCKING``,
        ``AUTOSKILLIT_HEADLESS``.

        Conditional keys (ten): ``AUTOSKILLIT_SESSION_TYPE``,
        ``AUTOSKILLIT_APPLICABLE_GUARDS``, ``AUTOSKILLIT_WRITE_GUARD_TOOL_NAMES``,
        ``SCENARIO_STEP_NAME``, ``CAMPAIGN_ID_ENV_VAR``, ``KITCHEN_SESSION_ID_ENV_VAR``,
        ``AUTOSKILLIT_ALLOWED_WRITE_PREFIX``, ``AUTOSKILLIT_ALLOWED_WRITE_PREFIXES``,
        ``AUTOSKILLIT_CWD``, ``AUTOSKILLIT_STATE_ROOT_ENV_VAR``. Each is included
        only when its input is non-empty (campaign/kitchen IDs are also read
        from the ambient ``os.environ``). A non-empty ``cwd`` supplies both the
        command's project context and the ``AUTOSKILLIT_STATE_ROOT`` signal
        guards use to locate ``.autoskillit/`` state in worktree topologies.
        """
        extras: dict[str, str] = dict(SHARED_BASELINE_ENV)
        extras["AUTOSKILLIT_HEADLESS"] = "1"
        if session_type:
            extras["AUTOSKILLIT_SESSION_TYPE"] = session_type
        if applicable_guards:
            extras[AUTOSKILLIT_APPLICABLE_GUARDS] = ",".join(sorted(applicable_guards))
        if write_guard_tool_names:
            extras[AUTOSKILLIT_WRITE_GUARD_TOOL_NAMES] = ",".join(sorted(write_guard_tool_names))
        if scenario_step_name:
            extras["SCENARIO_STEP_NAME"] = scenario_step_name
        campaign_id = os.environ.get(CAMPAIGN_ID_ENV_VAR)
        if campaign_id:
            extras[CAMPAIGN_ID_ENV_VAR] = campaign_id
        kitchen_session_id = os.environ.get(KITCHEN_SESSION_ID_ENV_VAR)
        if kitchen_session_id:
            extras[KITCHEN_SESSION_ID_ENV_VAR] = kitchen_session_id
        if write_prefix:
            extras["AUTOSKILLIT_ALLOWED_WRITE_PREFIX"] = write_prefix
        if write_prefixes:
            extras["AUTOSKILLIT_ALLOWED_WRITE_PREFIXES"] = ":".join(write_prefixes)
        if cwd:
            extras["AUTOSKILLIT_CWD"] = cwd
            extras[AUTOSKILLIT_STATE_ROOT_ENV_VAR] = cwd
        return extras

    def _apply_config(self, config: SkillSessionConfig) -> dict[str, Any]:
        """Unpack :class:`SkillSessionConfig` command-building fields into a plain dict.

        Separates shared fields (consumed by ``_assemble_shared_env_extras``)
        from backend-specific fields. The returned dict is the single source
        of truth that backend-specific builders can read from instead of
        accepting each field as a separate parameter.

        ``backend_override`` is intentionally excluded — it is consumed upstream
        at the headless layer before command builders are invoked.
        """
        return {
            "completion_marker": config.completion_marker,
            "model": config.model,
            "plugin_binding": config.plugin_binding,
            "output_format": config.output_format,
            "add_dirs": config.add_dirs,
            "exit_after_stop_delay_ms": config.exit_after_stop_delay_ms,
            "stream_idle_timeout_ms": config.stream_idle_timeout_ms,
            "scenario_step_name": config.scenario_step_name,
            "temp_dir_relpath": config.temp_dir_relpath,
            "allowed_write_prefix": config.allowed_write_prefix,
            "allowed_write_prefixes": config.allowed_write_prefixes,
            "provider_extras": config.provider_extras,
            "profile_name": config.profile_name,
            "resume_session_id": config.resume_session_id,
            "resume_checkpoint": config.resume_checkpoint,
            "resume_message": config.resume_message,
            "sandbox_mode": config.sandbox_mode,
            "network_access": config.network_access,
            "native_shell_capture_decision": config.native_shell_capture_decision,
            "managed_lineage_ref": config.managed_lineage_ref,
            "managed_attempt_id": config.managed_attempt_id,
        }


__all__ = [
    "BackendCmdBuilderBase",
    "FlagVocabulary",
    "SHARED_BASELINE_ENV",
    "_merge_caller_env_extras",
    "_managed_native_shell_env",
]
