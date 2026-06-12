"""Shared base class and types for backend command builder implementations.

Owns the eight env keys that every backend's skill-session and food-truck
cmd builders must inject (campaign id, kitchen session id, scenario step name,
allowed write prefixes, cwd, MCP token ceiling, MCP connection nonblocking).
Concrete backends (``ClaudeCodeBackend``, ``CodexBackend``) inherit from
:class:`BackendCmdBuilderBase` and contribute backend-specific extension
points (binary, sandbox default, env policy, flag vocabulary).

This module is IL-1 — it imports from ``autoskillit.core`` and
same-package modules only. It must NOT import from ``claude.py`` or
``codex.py`` to avoid a cyclic import.
"""

from __future__ import annotations

import abc
import os
from dataclasses import asdict, dataclass
from typing import Any, NamedTuple

from autoskillit.core import (
    CAMPAIGN_ID_ENV_VAR,
    KITCHEN_SESSION_ID_ENV_VAR,
    EnvPolicy,
    SkillSessionConfig,
)
from autoskillit.execution.backends._claude_prompt import _MAX_MCP_OUTPUT_TOKENS_VALUE


class FlagVocabulary(NamedTuple):
    """Per-backend CLI flag structure.

    Captures which flags a backend treats as variadic (may repeat with
    distinct values, e.g. ``--add-dir``) vs non-variadic, plus the canonical
    flag spellings for the four flags every backend shares (model, add-dir,
    resume, config override).
    """

    variadic_flags: frozenset[str]
    non_variadic_flags: frozenset[str]
    model_flag: str
    add_dir_flag: str
    resume_flag: str
    config_override_flag: str


@dataclass(frozen=True, slots=True)
class BackendCmdBuilderBase(abc.ABC):
    """Abstract base for backend cmd builders.

    Concrete subclasses (e.g. ``ClaudeCodeBackend``) supply the four
    extension points below and inherit the two concrete helpers. The base
    is intentionally minimal — it owns ONLY the eight shared env keys and
    the SkillSessionConfig unpacking helper. Backend-specific keys
    (``AUTOSKILLIT_HEADLESS``, ``AUTOSKILLIT_SESSION_TYPE``, Claude's
    ``CLAUDE_CODE_EXIT_AFTER_STOP_DELAY``, Codex's
    ``AUTOSKILLIT_COMPLETION_MARKER``, etc.) remain in each caller's local
    dict.
    """

    @property
    @abc.abstractmethod
    def _binary(self) -> str:
        """Return the CLI binary name (e.g. ``"claude"`` or ``"codex"``)."""

    @property
    @abc.abstractmethod
    def _sandbox_default(self) -> str:
        """Return the backend's default sandbox mode string.

        ``""`` for backends without a sandbox concept; ``"workspace-write"``
        for backends that default to workspace-write sandbox.
        """

    @property
    @abc.abstractmethod
    def _env_policy(self) -> EnvPolicy:
        """Return the backend's env policy instance."""

    @property
    @abc.abstractmethod
    def _flag_vocabulary(self) -> FlagVocabulary:
        """Return the backend's flag vocabulary describing its CLI surface."""

    def _assemble_shared_env_extras(
        self,
        *,
        scenario_step_name: str = "",
        allowed_write_prefix: str = "",
        allowed_write_prefixes: tuple[str, ...] = (),
        cwd: str = "",
    ) -> dict[str, str]:
        """Build the dict of env keys every backend injects.

        Always-set keys:
          - ``MAX_MCP_OUTPUT_TOKENS`` -> ``_MAX_MCP_OUTPUT_TOKENS_VALUE``
          - ``MCP_CONNECTION_NONBLOCKING`` -> ``"0"``

        Conditional keys (present only when their input is truthy or
        present in ``os.environ``):
          - ``CAMPAIGN_ID_ENV_VAR`` -> ``os.environ[...]``
          - ``KITCHEN_SESSION_ID_ENV_VAR`` -> ``os.environ[...]``
          - ``SCENARIO_STEP_NAME`` -> ``scenario_step_name``
          - ``AUTOSKILLIT_ALLOWED_WRITE_PREFIX`` -> ``allowed_write_prefix``
          - ``AUTOSKILLIT_ALLOWED_WRITE_PREFIXES`` -> colon-joined tuple
          - ``AUTOSKILLIT_CWD`` -> ``cwd``

        ``AUTOSKILLIT_SESSION_TYPE`` is intentionally NOT assembled here.
        It is a backend-specific concern: claude.py writes it from a local
        constant (``SESSION_TYPE_SKILL`` / ``SESSION_TYPE_ORCHESTRATOR``)
        and codex.py writes it via ``_codex_exec_extras``. Including it as
        a parameter here would be a structural trap for future callers.
        """
        extras: dict[str, str] = {
            "MAX_MCP_OUTPUT_TOKENS": _MAX_MCP_OUTPUT_TOKENS_VALUE,
            "MCP_CONNECTION_NONBLOCKING": "0",
        }
        campaign_id = os.environ.get(CAMPAIGN_ID_ENV_VAR)
        if campaign_id:
            extras[CAMPAIGN_ID_ENV_VAR] = campaign_id
        kitchen_session_id = os.environ.get(KITCHEN_SESSION_ID_ENV_VAR)
        if kitchen_session_id:
            extras[KITCHEN_SESSION_ID_ENV_VAR] = kitchen_session_id
        if scenario_step_name:
            extras["SCENARIO_STEP_NAME"] = scenario_step_name
        if allowed_write_prefix:
            extras["AUTOSKILLIT_ALLOWED_WRITE_PREFIX"] = allowed_write_prefix
        if allowed_write_prefixes:
            extras["AUTOSKILLIT_ALLOWED_WRITE_PREFIXES"] = ":".join(allowed_write_prefixes)
        if cwd:
            extras["AUTOSKILLIT_CWD"] = cwd
        return extras

    def _apply_config(self, config: SkillSessionConfig) -> dict[str, Any]:
        """Unpack a :class:`SkillSessionConfig` into a flat ``dict[str, Any]``.

        Round-trips all 18 fields of ``SkillSessionConfig``. Backends
        consume this dict when forwarding fields into per-backend env vars
        or CLI flags.
        """
        return asdict(config)


__all__ = ["BackendCmdBuilderBase", "FlagVocabulary"]
