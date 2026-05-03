"""Core skill name resolution and text-processing helpers.

Zero autoskillit imports outside this sub-package. Provides extract_skill_name,
extract_path_arg, resolve_target_skill, truncate_text, fleet_error, and session_type.
"""

from __future__ import annotations

import json
import os
import re
import warnings
from typing import Any

from ._type_constants import (
    AUTOSKILLIT_SKILL_PREFIX,
    FLEET_ERROR_CODES,
    HEADLESS_ENV_VAR,
    SESSION_TYPE_ENV_VAR,
    SKILL_COMMAND_PREFIX,
)
from ._type_enums import SessionType, SkillSource
from ._type_protocols_workspace import SkillResolver

__all__ = [
    "extract_path_arg",
    "extract_skill_name",
    "fleet_error",
    "resolve_skill_name",
    "resolve_target_skill",
    "session_type",
    "truncate_text",
]

_SKILL_CMD_RE = re.compile(
    r"^/(?:autoskillit:)?([\w-]+)"
)  # anchored: strict leading-slash for extraction
_SKILL_RESOLVE_RE = re.compile(
    r"/(?:autoskillit:)?([\w-]+)"
)  # unanchored: supports "Use /..." prefix forms

_PATH_PREFIXES: tuple[str, ...] = ("/", "./", ".autoskillit/")


def _looks_like_path(token: str) -> bool:
    return any(token.startswith(p) for p in _PATH_PREFIXES)


def extract_path_arg(skill_command: str) -> str | None:
    """Extract the first path-like positional argument from a skill_command string.

    Tolerates trailing text (markdown headers, extra tokens, embedded newlines)
    after the path. Returns None if no path-like token is found.
    Strips enclosing quotes from the returned path token.
    """
    stripped = skill_command.strip()
    m = _SKILL_CMD_RE.match(stripped)
    if m is None:
        return None
    tokens = stripped[m.end() :].split()
    for token in tokens:
        cleaned = token.strip('"').strip("'")
        if _looks_like_path(cleaned):
            return cleaned
    return None


def extract_skill_name(skill_command: str) -> str | None:
    """Extract the bare skill name from a skill_command string.

    Handles both ``/autoskillit:make-plan ...`` and ``/make-plan ...`` forms.
    Returns None if the command is not a slash-command.
    """
    m = _SKILL_CMD_RE.match(skill_command.strip())
    return m.group(1) if m else None


def resolve_skill_name(skill_command: str) -> str | None:
    """Extract and validate skill name from command string.

    Handles both ``/name`` and ``/autoskillit:name`` forms. Returns None if
    no match, name contains template expressions, or is followed by a
    bash-style ``{placeholder}`` token.
    """
    stripped = skill_command.strip()
    match = _SKILL_RESOLVE_RE.search(stripped)
    if not match:
        return None
    name = match.group(1)
    if "${{" in name:
        return None
    remainder = stripped[match.end() :]
    if remainder.startswith("{") or remainder.startswith("${{"):
        return None
    return name


def resolve_target_skill(
    skill_command: str,
    resolver: SkillResolver,
) -> tuple[str, str | None]:
    """Resolve a skill_command to the correct invocation namespace.

    Returns (resolved_command, skill_name).
    skill_name is None if skill_command is not a slash command.

    - Skills in ``skills/`` (BUNDLED) → ``/autoskillit:name`` namespace
    - Skills in ``skills_extended/`` (BUNDLED_EXTENDED) → ``/name`` namespace
    """
    name = extract_skill_name(skill_command)
    if name is None:
        return skill_command, None

    info = resolver.resolve(name)
    if info is None:
        return skill_command, name

    # Determine correct prefix based on physical location
    if info.source == SkillSource.BUNDLED:
        correct_prefix = AUTOSKILLIT_SKILL_PREFIX + name
    else:
        correct_prefix = SKILL_COMMAND_PREFIX + name

    # Reconstruct: replace the skill reference, preserve trailing arguments
    stripped = skill_command.strip()
    m = _SKILL_CMD_RE.match(stripped)
    if m is None:
        raise RuntimeError(f"regex failed after extract_skill_name succeeded: {stripped!r}")
    remainder = stripped[m.end() :]
    return correct_prefix + remainder, name


def truncate_text(text: str, max_len: int = 5000) -> str:
    """Truncate text to max_len, appending a count of truncated chars."""
    if len(text) <= max_len:
        return text
    return f"...[truncated {len(text) - max_len} chars]...\n" + text[-max_len:]


def fleet_error(
    code: str,
    message: str,
    *,
    details: dict[str, Any] | None = None,
) -> str:
    """Return canonical JSON error envelope for fleet dispatch failures.

    Validates that code is a registered FleetErrorCode. Raises ValueError
    for unregistered codes. The details dict must be JSON-serializable.
    """
    if code not in FLEET_ERROR_CODES:
        msg = f"Unregistered fleet error code: {code!r}"
        raise ValueError(msg)
    return json.dumps(
        {
            "success": False,
            "error": str(code),
            "user_visible_message": message,
            "details": details,
        }
    )


def session_type() -> SessionType:
    """Resolve current session type from AUTOSKILLIT_SESSION_TYPE env var.

    Fail-closed: returns LEAF on unset or invalid values.
    Transitional bridge: HEADLESS=1 without SESSION_TYPE emits DeprecationWarning.
    """
    raw = os.environ.get(SESSION_TYPE_ENV_VAR, "")
    if raw:
        raw_lower = raw.lower()
        try:
            return SessionType(raw_lower)
        except ValueError:
            warnings.warn(
                f"Invalid {SESSION_TYPE_ENV_VAR}={raw!r}, defaulting to LEAF. "
                f"Valid values: {', '.join(m.value for m in SessionType)}",
                DeprecationWarning,
                stacklevel=2,
            )
            return SessionType.LEAF
    if os.environ.get(HEADLESS_ENV_VAR) == "1":
        warnings.warn(
            f"{HEADLESS_ENV_VAR}=1 without {SESSION_TYPE_ENV_VAR} set. "
            "Defaulting to LEAF. Set AUTOSKILLIT_SESSION_TYPE explicitly.",
            DeprecationWarning,
            stacklevel=2,
        )
    return SessionType.LEAF
