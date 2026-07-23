"""Shared stdlib-only settings bridge for hook subprocesses.

Resolves quota settings from a layered hierarchy that mirrors the
dynaconf-backed settings system without importing third-party packages:

    1. Function parameter (``cache_path_override`` — for tests/DI)
    2. Environment variable (``AUTOSKILLIT_QUOTA_GUARD__<KEY>``) — highest runtime priority
    3. Hook config snapshot (``.autoskillit/temp/.hook_config.json``) — bridge from
       resolved settings
    4. Module default (matches ``config/defaults.yaml``) — lowest

Other hook policies consume the base/overlay snapshot through
``read_merged_hook_config``. This module is stdlib-only: no third-party
imports, no ``autoskillit.*`` imports. It runs unchanged under the bare
Python interpreter used by Claude Code hook subprocesses.
"""

import json
import os
import sys
import tempfile
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

# The exact keys the descriptor-anchored shell capture runner reads from
# hook_config["output_budget_policy"]. Keep this stdlib-only declaration in
# sync with _output_budget_policy_hook_payload() in server/tools/tools_kitchen.py.
OUTPUT_BUDGET_POLICY_HOOK_PAYLOAD_KEYS: frozenset[str] = frozenset(
    {"disabled", "shell_max_inline_bytes"}
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
        "configured_model",
        "profile_name",
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
    """Read the ``quota_guard`` section from the base hook config only.

    Returns ``{}`` if the base file is absent or unreadable. The base file
    is written by ``open_kitchen``. Overlay values are NOT consulted for
    quota settings — quota disablement for a session is signaled by a
    caller-session marker written by ``quota_guard_state_post_hook``, not
    by an overlay file key.
    """
    try:
        base_path = Path.cwd().joinpath(*HOOK_DIR_COMPONENTS, HOOK_CONFIG_FILENAME)
        if not base_path.exists():
            return {}
        base = json.loads(base_path.read_text())
    except (OSError, json.JSONDecodeError, AttributeError, TypeError):
        return {}
    if not isinstance(base, dict):
        return {}
    section = base.get("quota_guard")
    return section if isinstance(section, dict) else {}


def _resolve_quota_disable_state_dir() -> Path:
    """Resolve the per-session disable-marker state directory.

    Honors ``AUTOSKILLIT_STATE_DIR`` (override), then ``AUTOSKILLIT_CAMPAIGN_ID``
    (nested campaign subdir), and finally the default
    ``<cwd>/.autoskillit/temp/kitchen_state[/<campaign_id>]`` location.
    """
    state_override = os.environ.get("AUTOSKILLIT_STATE_DIR")
    campaign_id = os.environ.get("AUTOSKILLIT_CAMPAIGN_ID", "")
    if state_override:
        base = Path(state_override) / "kitchen_state"
    else:
        base = Path.cwd().joinpath(*HOOK_DIR_COMPONENTS, "kitchen_state")
    return base / campaign_id if campaign_id else base


def _validate_session_id(session_id: str) -> str:
    """Reject empty or path-traversal session IDs. Returns the validated ID."""
    if not isinstance(session_id, str) or not session_id:
        raise ValueError("session_id must be a non-empty string")
    if os.sep in session_id or (os.altsep and os.altsep in session_id):
        raise ValueError(f"session_id must not contain path separators: {session_id!r}")
    if session_id in {".", ".."} or session_id.startswith("./") or session_id.startswith("../"):
        raise ValueError(f"session_id must not be a path-relative segment: {session_id!r}")
    if any(c.isspace() for c in session_id):
        raise ValueError(f"session_id must not contain whitespace: {session_id!r}")
    return session_id


def quota_disable_marker_path(session_id: str) -> Path:
    """Resolve the per-session quota-disable marker path. Raises on invalid IDs."""
    _validate_session_id(session_id)
    return _resolve_quota_disable_state_dir() / f"{session_id}_quota_guard_disabled.json"


def _atomic_write_marker(marker_path: Path, payload: str) -> None:
    """Atomic write for a small JSON marker (mirrors recipe_confirmed_post_hook pattern)."""
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(marker_path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
        os.replace(tmp, str(marker_path))
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


MARKER_TTL_SECONDS = 24 * 3600


def write_quota_disable_marker(session_id: str) -> None:
    """Atomically write a fresh quota-disable marker for the given session_id."""
    marker_path = quota_disable_marker_path(session_id)
    payload = json.dumps(
        {
            "session_id": session_id,
            "disabled_at": datetime.now(UTC).isoformat(),
            "marker_version": 1,
        }
    )
    _atomic_write_marker(marker_path, payload)


def read_quota_disable_marker(session_id: str) -> dict | None:
    """Return the parsed marker payload for ``session_id`` if fresh and matching.

    Returns ``None`` for missing, malformed, mismatched, traversal-shaped, or
    expired markers. Never raises.
    """
    try:
        _validate_session_id(session_id)
    except ValueError:
        return None
    marker_path = quota_disable_marker_path(session_id)
    try:
        raw = marker_path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    if data.get("session_id") != session_id:
        return None
    disabled_at_raw = data.get("disabled_at")
    if not isinstance(disabled_at_raw, str):
        return None
    try:
        disabled_at = datetime.fromisoformat(disabled_at_raw)
    except (ValueError, TypeError):
        return None
    if disabled_at.tzinfo is None:
        return None
    age = (datetime.now(UTC) - disabled_at).total_seconds()
    if age > MARKER_TTL_SECONDS or age < -MARKER_TTL_SECONDS:
        return None
    return data


def clear_quota_disable_marker(session_id: str) -> None:
    """Remove the quota-disable marker for ``session_id``. No-op if absent."""
    try:
        marker_path = quota_disable_marker_path(session_id)
    except ValueError:
        return
    try:
        marker_path.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass


def is_quota_guard_disabled_for_session(session_id: str) -> bool:
    """Return True iff a fresh, matching quota-disable marker exists for this session."""
    if not session_id:
        return False
    return read_quota_disable_marker(session_id) is not None


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
