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
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

# Keep in sync with _HOOK_CONFIG_PATH_COMPONENTS in hooks/_fmt_primitives.py
# (stdlib-only boundary prevents a shared import).
HOOK_CONFIG_FILENAME = ".hook_config.json"
HOOK_CONFIG_OVERLAY_FILENAME = ".hook_config_overlay.json"
HOOK_DIR_COMPONENTS = (".autoskillit", "temp")

DEFAULT_CACHE_PATH = "~/.claude/autoskillit_quota_cache.json"
DEFAULT_CACHE_MAX_AGE = 300
DEFAULT_BUFFER_SECONDS = 60

ENV_CACHE_PATH = "AUTOSKILLIT_QUOTA_GUARD__CACHE_PATH"
ENV_CACHE_MAX_AGE = "AUTOSKILLIT_QUOTA_GUARD__CACHE_MAX_AGE"
ENV_BUFFER_SECONDS = "AUTOSKILLIT_QUOTA_GUARD__BUFFER_SECONDS"
ENV_DISABLED = "AUTOSKILLIT_QUOTA_GUARD__DISABLED"

# The exact keys this module reads from hook_config["quota_guard"].
# The bridge contract test asserts equality between this set and the
# serializer's payload keys — update both together.
QUOTA_GUARD_HOOK_PAYLOAD_KEYS: frozenset[str] = frozenset(
    {"cache_path", "cache_max_age", "buffer_seconds", "disabled"}
)

# The exact keys that appear in token_usage.json files written by flush_session_log.
# The bridge contract test asserts equality between this set and TokenUsageFileEntry
# annotations — update both together.
#
# v1 field aliases (Anthropic API names) are mapped in _V1_TOKEN_FIELD_ALIASES below.
# Read-side consumers use the alias map for backward-compatible dual-key fallback.
TOKEN_USAGE_FILE_KEYS: frozenset[str] = frozenset(
    {
        "session_label",
        "input_tokens",
        "output_tokens",
        "cache_write_tokens",
        "cache_read_tokens",
        "peak_context",
        "turn_count",
        "timing_seconds",
        "order_id",
        "loc_insertions",
        "loc_deletions",
        "provider_used",
        "model_identifier",
        "dispatch_id",
        "campaign_id",
        "schema_version",
    }
)

# Mapping from v1 on-disk field names (Anthropic API names, schema_version < 2)
# to their canonical v2 equivalents. Read-side consumers use this for dual-key
# fallback when reading older token_usage.json files.
_V1_TOKEN_FIELD_ALIASES: dict[str, str] = {
    "cache_creation_input_tokens": "cache_write_tokens",
    "cache_read_input_tokens": "cache_read_tokens",
}


@dataclass(frozen=True, slots=True)
class QuotaHookSettings:
    """Resolved settings for quota guard hooks."""

    cache_path: str
    cache_max_age: int
    buffer_seconds: int
    disabled: bool = False


def merge_hook_configs(base: dict, overlay: dict) -> dict:
    """Merge base and overlay hook config dicts (overlay wins, shallow dict merge)."""
    merged = dict(base)
    for k, v in overlay.items():
        if k in merged and isinstance(merged[k], dict) and isinstance(v, dict):
            merged[k] = {**merged[k], **v}
        else:
            merged[k] = v
    return merged


def read_merged_hook_config(root: Path | None = None) -> dict:
    """Read and merge base + overlay hook config files (stdlib-only).

    Returns ``{}`` if both files are absent or unreadable.
    """
    cwd = root if root is not None else Path.cwd()
    try:
        base_path = cwd.joinpath(*HOOK_DIR_COMPONENTS, HOOK_CONFIG_FILENAME)
        overlay_path = cwd.joinpath(*HOOK_DIR_COMPONENTS, HOOK_CONFIG_OVERLAY_FILENAME)
        base = json.loads(base_path.read_text()) if base_path.exists() else {}
        overlay = json.loads(overlay_path.read_text()) if overlay_path.exists() else {}
        return merge_hook_configs(base, overlay)
    except (OSError, json.JSONDecodeError, AttributeError, TypeError):
        return {}


def _read_hook_config() -> dict:
    """Read the merged ``quota_guard`` section from base + overlay hook config.

    Returns ``{}`` if both files are absent or unreadable. The base file is
    written by ``open_kitchen`` and the overlay by ``disable_quota_guard``.
    Overlay values take precedence over base values.
    """
    return read_merged_hook_config().get("quota_guard", {})


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

    env_disabled = os.environ.get(ENV_DISABLED, "").strip().lower()
    if env_disabled in ("1", "true", "yes"):
        disabled = True
    elif env_disabled in ("0", "false", "no"):
        disabled = False
    else:
        disabled = bool(hook_config.get("disabled", False))

    return QuotaHookSettings(
        cache_path=cache_path,
        cache_max_age=cache_max_age,
        buffer_seconds=buffer_seconds,
        disabled=disabled,
    )


_AUTOSKILLIT_LOG_DIR_ENV = "AUTOSKILLIT_LOG_DIR"


def read_quota_cache(cache_path_str: str, max_age: int) -> dict | None:
    """Read quota cache file. Returns parsed data or None if missing/stale/corrupt."""
    cache_path = Path(cache_path_str).expanduser()
    if not cache_path.is_file():
        return None
    try:
        data = json.loads(cache_path.read_text())
        fetched = datetime.fromisoformat(data["fetched_at"])
        age = (datetime.now(UTC) - fetched).total_seconds()
        if age > max_age:
            return None
        return data
    except (json.JSONDecodeError, KeyError, ValueError, OSError, TypeError):
        return None


def resolve_quota_log_dir(*, caller: str = "") -> Path | None:
    """Resolve the autoskillit log root directory. Returns None on any error.

    Priority: AUTOSKILLIT_LOG_DIR env var > platform default.
    Mirrors the logic in execution/session_log.py:resolve_log_dir().
    """
    try:
        override = os.environ.get(_AUTOSKILLIT_LOG_DIR_ENV)
        if override:
            return Path(override)
        if sys.platform == "darwin":
            return Path.home() / "Library" / "Application Support" / "autoskillit" / "logs"
        xdg = os.environ.get("XDG_DATA_HOME")
        if xdg:
            return Path(xdg) / "autoskillit" / "logs"
        return Path.home() / ".local" / "share" / "autoskillit" / "logs"
    except Exception as exc:
        if caller:
            print(f"{caller}: failed to resolve log directory: {exc}", file=sys.stderr)
        return None


def write_quota_log_event(event: dict, log_dir: Path | None, *, caller: str = "") -> None:
    """Append a quota event to quota_events.jsonl at the log root.

    No-ops when ``log_dir`` is None. On write failure, prints to stderr when
    ``caller`` is provided; otherwise silently returns.
    """
    if log_dir is None:
        return
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        line = json.dumps(event) + "\n"
        with open(log_dir / "quota_events.jsonl", "a", encoding="utf-8") as f:
            f.write(line)
    except Exception as exc:
        if caller:
            print(f"{caller}: failed to write quota log event: {exc}", file=sys.stderr)
