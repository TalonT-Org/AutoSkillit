"""Shared stdlib-only settings resolver for quota guard hooks.

Resolves hook settings from a layered hierarchy that mirrors the
dynaconf-backed settings system without importing third-party packages:

    1. Function parameter (``cache_path_override`` — for tests/DI)
    2. Environment variable (``AUTOSKILLIT_QUOTA_GUARD__<KEY>``) — highest runtime priority
    3. Hook config snapshot (``.autoskillit/temp/.hook_config.json``) — bridge from
       resolved settings
    4. Module default (matches ``config/defaults.yaml``) — lowest

This module is stdlib-only: no third-party imports, no ``autoskillit.*``
imports. It runs unchanged under the bare Python interpreter used by
Claude Code hook subprocesses.
"""

import json
import os
from dataclasses import dataclass
from pathlib import Path

HOOK_CONFIG_FILENAME = ".hook_config.json"
HOOK_DIR_COMPONENTS = (".autoskillit", "temp")

DEFAULT_CACHE_PATH = "~/.claude/autoskillit_quota_cache.json"
DEFAULT_CACHE_MAX_AGE = 300
DEFAULT_BUFFER_SECONDS = 60

ENV_CACHE_PATH = "AUTOSKILLIT_QUOTA_GUARD__CACHE_PATH"
ENV_CACHE_MAX_AGE = "AUTOSKILLIT_QUOTA_GUARD__CACHE_MAX_AGE"
ENV_BUFFER_SECONDS = "AUTOSKILLIT_QUOTA_GUARD__BUFFER_SECONDS"


@dataclass(frozen=True, slots=True)
class QuotaHookSettings:
    """Resolved settings for quota guard hooks."""

    cache_path: str
    cache_max_age: int
    buffer_seconds: int
    disabled: bool = False


def _read_hook_config() -> dict:
    """Read the ``quota_guard`` section of ``<cwd>/.autoskillit/temp/.hook_config.json``.

    Returns ``{}`` if the file is absent or unreadable. This file is written by
    ``open_kitchen`` and removed by ``close_kitchen``.
    """
    try:
        config_path = Path.cwd().joinpath(*HOOK_DIR_COMPONENTS, HOOK_CONFIG_FILENAME)
        return json.loads(config_path.read_text()).get("quota_guard", {})
    except (OSError, json.JSONDecodeError, AttributeError, TypeError):
        return {}


def _resolve_int(env_var: str, hook_value: object, default: int) -> int:
    """Resolve an integer setting: env var > hook config > default.

    Non-numeric env var values fall through to the next level.
    """
    env_raw = os.environ.get(env_var)
    if env_raw is not None:
        try:
            return int(env_raw)
        except (ValueError, TypeError):
            pass
    if isinstance(hook_value, int) and not isinstance(hook_value, bool):
        return hook_value
    if isinstance(hook_value, float):
        return int(hook_value)
    return default


def resolve_quota_settings(*, cache_path_override: str | None = None) -> QuotaHookSettings:
    """Resolve quota hook settings from the layered hierarchy.

    ``cache_path``: ``cache_path_override`` > env var > hook config > default.
    ``cache_max_age`` / ``buffer_seconds``: env var > hook config > default.
    """
    hook_config = _read_hook_config()

    cache_path = (
        cache_path_override
        or os.environ.get(ENV_CACHE_PATH)
        or hook_config.get("cache_path")
        or DEFAULT_CACHE_PATH
    )

    cache_max_age = _resolve_int(
        ENV_CACHE_MAX_AGE,
        hook_config.get("cache_max_age"),
        DEFAULT_CACHE_MAX_AGE,
    )

    buffer_seconds = _resolve_int(
        ENV_BUFFER_SECONDS,
        hook_config.get("buffer_seconds"),
        DEFAULT_BUFFER_SECONDS,
    )

    disabled = bool(hook_config.get("disabled", False))

    return QuotaHookSettings(
        cache_path=cache_path,
        cache_max_age=cache_max_age,
        buffer_seconds=buffer_seconds,
        disabled=disabled,
    )
