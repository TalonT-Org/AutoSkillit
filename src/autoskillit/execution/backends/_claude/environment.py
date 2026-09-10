"""Claude repository and process environment policy."""

from __future__ import annotations

import json
from collections.abc import Mapping
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from pathlib import Path

from packaging.version import Version

from autoskillit.core import (
    AUTOSKILLIT_ATTESTED_CLIENT_GATE_TOKENS,
    AUTOSKILLIT_ATTESTED_META_SUPPORT,
    CLAUDE_ANNOTATION_SUPPORT_MIN_VERSION,
    CLAUDE_INJECTED_CLIENT_RESULT_TOKENS,
    ResumeSpec,
    SessionAttemptHandle,
    atomic_write,
    build_agent_env,
)
from autoskillit.execution.process import INTERACTIVE_TETHER_CEILING_SECONDS

CLAUDE_AGENT_TEAMS_ENV_VAR: str = "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS"
_AGENT_TEAMS_SETTINGS_CANDIDATE_NAMES = (
    ".claude/settings.json",
    ".claude/settings.local.json",
)
_AGENT_TEAMS_TRUTHY = frozenset({"1", "true", "yes", "on"})


def _ignore_child_identity(pid: int, pgid: int) -> None:
    del pid, pgid


class ClaudeCookSupportMixin:
    def recover_cook_history(self) -> None:
        return None

    def session_attempt_context(
        self,
        *,
        session_home: Path,
        project_dir: Path,
        launch_id: str,
        attempt: int,
        current_resume_spec: ResumeSpec,
        ceiling_seconds: float = INTERACTIVE_TETHER_CEILING_SECONDS,
    ) -> AbstractContextManager[SessionAttemptHandle]:
        del session_home, project_dir, launch_id, attempt, current_resume_spec, ceiling_seconds
        return nullcontext(
            SessionAttemptHandle(
                view_id="",
                pass_fds=(),
                _record_spawn=_ignore_child_identity,
                _record_reaped=_ignore_child_identity,
            )
        )


def _neutralize_agent_teams_env(env: dict[str, str]) -> None:
    """Remove ``CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS`` from ``env`` in place."""
    env.pop(CLAUDE_AGENT_TEAMS_ENV_VAR, None)


def _agent_teams_settings_candidates(root: Path) -> tuple[Path, ...]:
    """Return the absolute candidate settings paths under ``root``."""
    return tuple(root / name for name in _AGENT_TEAMS_SETTINGS_CANDIDATE_NAMES)


def detect_repository_agent_teams_setting(
    project_root: Path | str | None,
) -> tuple[str | None, str]:
    """Return (effective_value, source_path) for any conflicting settings file.

    Per Claude Code's documented settings precedence, ``env.<var>`` entries
    in ``.claude/settings.json`` or ``.claude/settings.local.json`` apply
    after user-level settings and can re-enable teams even when the
    launcher process env has the var unset.

    Returns ``(None, "")`` when no conflicting entry is found. The caller
    must combine the launcher-env scan with this file scan and refuse the
    launch when neither confirms an inactive effective state.
    """
    if project_root is None:
        return (None, "")
    root = Path(project_root).expanduser().resolve()
    for candidate in _agent_teams_settings_candidates(root):
        try:
            content = candidate.read_text(encoding="utf-8")
        except (FileNotFoundError, OSError):
            continue
        try:
            parsed = json.loads(content)
        except (ValueError, TypeError):
            continue
        if not isinstance(parsed, dict):
            continue
        env = parsed.get("env")
        if not isinstance(env, dict):
            continue
        value = env.get(CLAUDE_AGENT_TEAMS_ENV_VAR)
        if isinstance(value, str):
            return (value, str(candidate))
    return (None, "")


def find_malformed_agent_teams_settings(
    project_root: Path | str | None,
) -> list[str]:
    """Return paths of settings files that exist but cannot be parsed.

    When ``force_inactive_agent_teams=True`` is requested, a malformed
    settings file is a fail-closed condition: Claude Code may still parse
    the file permissively and re-enable teams. Returns an empty list when
    the project_root is None or no settings files are malformed.
    """
    if project_root is None:
        return []
    root = Path(project_root).expanduser().resolve()
    malformed: list[str] = []
    for candidate in _agent_teams_settings_candidates(root):
        try:
            content = candidate.read_text(encoding="utf-8")
        except FileNotFoundError:
            continue
        except OSError:
            # Unreadable file: treat as malformed for fail-closed purposes.
            malformed.append(str(candidate))
            continue
        try:
            parsed = json.loads(content)
        except (ValueError, TypeError):
            malformed.append(str(candidate))
            continue
        if not isinstance(parsed, dict):
            malformed.append(str(candidate))
            continue
        env = parsed.get("env")
        if env is not None and not isinstance(env, dict):
            malformed.append(str(candidate))
    return malformed


def _active_agent_teams(value: str) -> bool:
    """Return True if the string value would re-enable agent teams."""
    return value.strip().lower() in _AGENT_TEAMS_TRUTHY


def neutralize_repository_agent_teams_settings(project_root: Path | str | None) -> int:
    """Strip conflicting ``env.CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS`` entries.

    Returns the number of settings files modified. Each file is rewritten
    after the offending key is removed. Refuses to rewrite when the file
    is malformed or unreadable.
    """
    if project_root is None:
        return 0
    root = Path(project_root).expanduser().resolve()
    modified = 0
    for candidate in _agent_teams_settings_candidates(root):
        try:
            content = candidate.read_text(encoding="utf-8")
        except (FileNotFoundError, OSError):
            continue
        try:
            parsed = json.loads(content)
        except (ValueError, TypeError):
            continue
        if not isinstance(parsed, dict):
            continue
        env = parsed.get("env")
        if not isinstance(env, dict):
            continue
        if CLAUDE_AGENT_TEAMS_ENV_VAR not in env:
            continue
        del env[CLAUDE_AGENT_TEAMS_ENV_VAR]
        try:
            new_content = json.dumps(parsed, indent=2, sort_keys=True)
        except (ValueError, TypeError):
            continue
        atomic_write(candidate, new_content)
        modified += 1
    return modified


def _resolve_project_root_for_inactive_check(project_root: Path | str | None) -> None:
    """Refuse a launch when force_inactive is requested without project_root.

    Without ``project_root``, ``assert_agent_teams_inactive`` cannot read
    the target repo's ``.claude/settings*.json`` files, so the only path
    it can confirm is the resolved launcher env. The plan's Step 5 (3)
    requires a positive confirmation of BOTH the env and the settings
    files; passing ``None`` is a fail-open bypass.
    """
    if project_root is None:
        raise RuntimeError(
            "force_inactive_agent_teams=True requires project_root so the "
            "settings file scan can confirm inactivity"
        )


def _interactive_invocation_environment_policy(
    env: Mapping[str, str],
    project_root: Path | str | None,
) -> list[str]:
    """Content-policy errors for an interactive Claude launch.

    The interactive cook/order checkpoint must positively confirm that the
    effective environment will leave Claude agent teams inactive. Returns
    a list of human-readable error strings (empty list when no violation
    is detected). The launch layer surfaces these as pre-spawn failures.

    The policy matches what the per-builder assertions check — the launch
    env must not carry a truthy ``CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS``
    value, and any conflicting entry in the target repository's
    ``.claude/settings*.json`` files would re-enable teams under Claude's
    documented settings precedence.
    """
    errors: list[str] = []
    env_value = env.get(CLAUDE_AGENT_TEAMS_ENV_VAR)
    if isinstance(env_value, str) and _active_agent_teams(env_value):
        errors.append(
            f"{CLAUDE_AGENT_TEAMS_ENV_VAR}={env_value!r} is set in the launch "
            f"environment; Claude agent teams would be active at launch"
        )
    malformed = find_malformed_agent_teams_settings(project_root)
    if malformed:
        errors.append(
            f"settings file(s) could not be parsed and may re-enable teams: "
            f"{', '.join(malformed)}; repair or remove the malformed file before launching"
        )
    file_value, file_path = detect_repository_agent_teams_setting(project_root)
    if file_value is not None and _active_agent_teams(file_value):
        errors.append(
            f"{CLAUDE_AGENT_TEAMS_ENV_VAR}={file_value!r} is set in "
            f"{file_path}; Claude agent teams would be re-enabled by "
            "repository settings precedence"
        )
    return errors


def assert_agent_teams_inactive(
    env: Mapping[str, str],
    project_root: Path | str | None,
    *,
    force_inactive: bool,
) -> None:
    """Verify that the effective environment will result in inactive agent teams.

    Raises ``RuntimeError`` when ``force_inactive`` is True but neither the
    process env nor the target repository's settings files positively
    confirm an inactive policy. This is the pre-spawn refusal surface.

    A malformed settings file is also a fail-closed condition: Claude Code
    may still parse the file permissively and re-enable teams.
    """
    if not force_inactive:
        return
    env_value = env.get(CLAUDE_AGENT_TEAMS_ENV_VAR)
    if isinstance(env_value, str) and _active_agent_teams(env_value):
        raise RuntimeError(
            f"force_inactive_agent_teams requested but {CLAUDE_AGENT_TEAMS_ENV_VAR} "
            f"is set to {env_value!r} in the launch env"
        )
    malformed = find_malformed_agent_teams_settings(project_root)
    if malformed:
        raise RuntimeError(
            f"force_inactive_agent_teams requested but settings file(s) could not "
            f"be parsed and may re-enable teams: {', '.join(malformed)}. "
            "Repair or remove the malformed file before launching."
        )
    file_value, file_path = detect_repository_agent_teams_setting(project_root)
    if file_value is not None and _active_agent_teams(file_value):
        raise RuntimeError(
            f"force_inactive_agent_teams requested but {CLAUDE_AGENT_TEAMS_ENV_VAR} "
            f"is set to {file_value!r} in {file_path}"
        )


def _claude_host_attestation_env(
    installed_version: Version | None,
) -> dict[str, str]:
    """Build the host client attestation env for one Claude-launched session.

    Carries the launcher's attestation of what the connected Claude Code host
    client supports to the MCP server — read once at server startup (see
    ``server.recipe._recipe_delivery``) and used as the conservative-default source
    for recipe-delivery decisions.

    ``annotation_support`` is derived from the installed CLI version probed by
    ``ensure_pre_launch()`` — not hardcoded. Below 2.1.91, annotation metadata
    is stripped by the client and tool results fall back to token-gated
    ``MAX_MCP_OUTPUT_TOKENS`` only. When the installed version is unknown
    (pre-launch probe not yet run), annotation support defaults to ``"0"``
    (conservative fallback).

    Deliberately NOT part of ``SHARED_BASELINE_ENV``: Codex has its own
    receipt-based protected recipe-delivery pipeline and must never be told it
    has annotation support.
    """
    meta_support = (
        "1"
        if installed_version is not None
        and installed_version >= Version(CLAUDE_ANNOTATION_SUPPORT_MIN_VERSION)
        else "0"
    )
    return {
        AUTOSKILLIT_ATTESTED_CLIENT_GATE_TOKENS: str(CLAUDE_INJECTED_CLIENT_RESULT_TOKENS),
        AUTOSKILLIT_ATTESTED_META_SUPPORT: meta_support,
    }


@dataclass(frozen=True, slots=True)
class ClaudeEnvPolicy:
    def build_env(
        self,
        base_env: Mapping[str, str],
        *,
        extras: Mapping[str, str] | None = None,
        required: frozenset[str] | None = None,
    ) -> dict[str, str]:
        return dict(build_agent_env(base=base_env, extras=extras, required=required))
