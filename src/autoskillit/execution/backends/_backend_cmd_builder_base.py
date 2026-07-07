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

import os
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, NamedTuple

from autoskillit.core import (
    AUTOSKILLIT_APPLICABLE_GUARDS,
    AUTOSKILLIT_WRITE_GUARD_TOOL_NAMES,
    CAMPAIGN_ID_ENV_VAR,
    KITCHEN_SESSION_ID_ENV_VAR,
    SkillSessionConfig,
)

if TYPE_CHECKING:
    from autoskillit.core import EnvPolicy


# Injected into every AutoSkillit-launched headless and cook session.
# Raises the Claude Code client-side MCP tool result size gate from the
# default 25,000 tokens to 50,000, preventing open_kitchen() responses
# from being persisted to a file instead of returned inline.
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

        Conditional keys (nine): ``AUTOSKILLIT_SESSION_TYPE``,
        ``AUTOSKILLIT_APPLICABLE_GUARDS``, ``AUTOSKILLIT_WRITE_GUARD_TOOL_NAMES``,
        ``SCENARIO_STEP_NAME``, ``CAMPAIGN_ID_ENV_VAR``, ``KITCHEN_SESSION_ID_ENV_VAR``,
        ``AUTOSKILLIT_ALLOWED_WRITE_PREFIX``, ``AUTOSKILLIT_ALLOWED_WRITE_PREFIXES``,
        ``AUTOSKILLIT_CWD``. Each is included only when its input is non-empty
        (campaign/kitchen IDs are also read from the ambient ``os.environ``).
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
            "plugin_source": config.plugin_source,
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
        }


__all__ = [
    "BackendCmdBuilderBase",
    "FlagVocabulary",
    "SHARED_BASELINE_ENV",
]
