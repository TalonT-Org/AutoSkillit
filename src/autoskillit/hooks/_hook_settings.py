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

import importlib
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
    {"disabled", "shell_max_inline_bytes", "capture_capacity"}
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

_MAPPING_OVERLAY_DOMAINS: frozenset[str] = frozenset(
    {
        "order",
        "fleet",
        "core",
        "locked_ingredients",
        "locked_steps",
        "git_ops_policy",
        "quota_guard",
    }
)


@dataclass(frozen=True, slots=True)
class QuotaHookSettings:
    """Resolved settings for quota guard hooks."""

    cache_path: str
    cache_max_age: int
    buffer_seconds: int
    disabled: bool = False


def merge_hook_configs(base: dict, overlay: dict) -> dict:
    """Merge base and overlay hook config dicts (overlay wins, shallow dict merge)."""
    if not isinstance(base, dict) or not isinstance(overlay, dict):
        raise TypeError("hook configuration roots must be mappings")
    for source in (base, overlay):
        for domain in _MAPPING_OVERLAY_DOMAINS:
            if domain in source and not isinstance(source[domain], dict):
                raise TypeError(f"hook configuration domain {domain!r} must be a mapping")
    merged = dict(base)
    for k, v in overlay.items():
        if k in merged and isinstance(merged[k], dict) and isinstance(v, dict):
            merged[k] = {**merged[k], **v}
        else:
            merged[k] = v
    return merged


def _default_state_root() -> Path:
    """Bare-default state root: ``AUTOSKILLIT_STATE_ROOT`` env var, else process cwd.

    Mirrors the env-var tier of ``hooks/_hook_payload.py``'s ``resolve_state_root``
    for callers with no payload cwd to walk from. Kept as an inline duplicate
    (not an import) to preserve this module's stdlib-only, zero-sibling-import
    boundary — this module is imported both as a bare sibling module (guard
    subprocesses that pre-insert ``hooks/`` onto ``sys.path``) and via the
    normal package path (in-process test imports), and only the latter would
    resolve a cross-module sibling import correctly.
    """
    env_root = os.environ.get("AUTOSKILLIT_STATE_ROOT")
    if env_root:
        return Path(env_root).resolve()
    return Path.cwd()


def read_merged_hook_config(root: Path | None = None) -> dict:
    """Read and merge base + overlay hook config files (stdlib-only).

    Returns ``{}`` if both files are absent or unreadable.
    """
    cwd = root if root is not None else _default_state_root()
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
        base_path = _default_state_root().joinpath(*HOOK_DIR_COMPONENTS, HOOK_CONFIG_FILENAME)
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
        base = _default_state_root().joinpath(*HOOK_DIR_COMPONENTS, "kitchen_state")
    return base / campaign_id if campaign_id else base


def validate_session_id(session_id: str) -> str:
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
    validate_session_id(session_id)
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
        validate_session_id(session_id)
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


def _resolve_int(env_raw: str | None, hook_value: object, default: int) -> int:
    """Resolve an integer setting: env var > hook config > default.

    Non-numeric env var values fall through to the next level.
    """
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
        os.environ.get(ENV_CACHE_MAX_AGE),
        hook_config.get("cache_max_age"),
        DEFAULT_CACHE_MAX_AGE,
    )

    buffer_seconds = _resolve_int(
        os.environ.get(ENV_BUFFER_SECONDS),
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


#: Oldest-first line bound applied on every write to either JSONL sink in this module.
#: Duplicated (not imported) from core.runtime._reclamation.append_and_trim_jsonl's shape --
#: this module is stdlib-only with no autoskillit.* imports by design (see module docstring),
#: same boundary that already duplicates HOOK_CONFIG_PATH_COMPONENTS instead of sharing it.
_MAX_HOOK_LOG_LINES = 5000


def _append_and_trim_jsonl_line(path: Path, line: str, *, max_lines: int) -> None:
    existing: list[str] = []
    if path.exists():
        existing = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    existing.append(line)
    if max_lines > 0 and len(existing) > max_lines:
        existing = existing[-max_lines:]
    _atomic_write_marker(path, "\n".join(existing) + "\n")


def write_quota_log_event(event: dict, log_dir: Path | None, *, caller: str = "") -> None:
    """Append a quota event to quota_events.jsonl at the log root, bounded to
    _MAX_HOOK_LOG_LINES.

    No-ops when ``log_dir`` is None. On write failure, prints to stderr when
    ``caller`` is provided; otherwise silently returns.
    """
    if log_dir is None:
        return
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        _append_and_trim_jsonl_line(
            log_dir / "quota_events.jsonl", json.dumps(event), max_lines=_MAX_HOOK_LOG_LINES
        )
    except Exception as exc:
        if caller:
            print(f"{caller}: failed to write quota log event: {exc}", file=sys.stderr)


#: Bounded set of allowed join-diagnostic record keys. Anything else is
#: stripped before write so child bodies, prompts, secrets, and private
#: task IDs never land in the diagnostic sink.
DIAGNOSTIC_KEYS: frozenset[str] = frozenset(
    {
        "ts",
        "session_id",
        "top_level_parent",
        "join_batch_id",
        "assignment",
        "tool_use_id",
        "skill_name",
        "semantic_digest",
        "adaptation_digest",
        "artifact_digest",
        "artifact_incarnation",
        "source_artifact_digest",
        "source_artifact_incarnation_id",
        "managed_parent_id",
        "managed_leaf_id",
        "assignment_id",
        "attempt_id",
        "run_id",
        "terminal_event_id",
        "terminal_payload_digest",
        "lifecycle_state",
        "selector_presence",
        "activation_source",
        "launch_policy_state",
        "status",
        "public_child_id",
        "team_name",
        "execution_mode",
        "wave_outcome",
        "gate",
        "binding_valid",
    }
)


def write_join_diagnostic(record: dict, *, caller: str = "") -> None:
    """Append one bounded join-gate diagnostic record to ``join_diagnostics.jsonl``.

    The record is redacted to ``DIAGNOSTIC_KEYS`` before write.
    Child bodies, prompts, secrets, and private task IDs are never persisted.
    No-ops when the resolved log dir is None.
    """
    bounded = {key: value for key, value in record.items() if key in DIAGNOSTIC_KEYS}
    bounded.setdefault("ts", datetime.now(UTC).isoformat())
    log_dir = resolve_quota_log_dir(caller=caller or "join_diagnostic")
    if log_dir is None:
        return
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        _append_and_trim_jsonl_line(
            log_dir / "join_diagnostics.jsonl",
            json.dumps(bounded, sort_keys=True),
            max_lines=_MAX_HOOK_LOG_LINES,
        )
    except Exception as exc:
        if caller:
            print(f"{caller}: failed to write join diagnostic: {exc}", file=sys.stderr)


def write_dispatch_diagnostic(
    event_kind: str,
    logical_hook_name: str,
    reason: str,
) -> None:
    """Append one bounded dispatcher-degradation record without masking the hook."""
    record = {
        "ts": datetime.now(UTC).isoformat(),
        "event_kind": str(event_kind)[:64],
        "logical_hook_name": str(logical_hook_name)[:256],
        "reason": str(reason)[:512],
    }
    log_dir = resolve_quota_log_dir(caller="hook_dispatch")
    if log_dir is None:
        return
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        _append_and_trim_jsonl_line(
            log_dir / "hook_dispatch_diagnostics.jsonl",
            json.dumps(record, sort_keys=True),
            max_lines=_MAX_HOOK_LOG_LINES,
        )
    except Exception:
        return


def read_session_binding(payload_cwd: str, session_id: str) -> dict[str, object] | None:
    """Read the binding identified by the hook payload.

    Missing, unreadable, malformed, or mismatched binding artifacts retain the
    existing permissive policy and are treated as no binding.
    """
    # Some projected hooks consume settings without carrying join artifacts, so
    # load this dependency only on the join-binding path.
    module_name = f"{__package__}._session_binding" if __package__ else "_session_binding"
    binding_module = importlib.import_module(module_name)
    resolve_path = getattr(binding_module, "resolve_binding_path")
    read_binding = getattr(binding_module, "read_binding")
    binding_error = getattr(binding_module, "SessionBindingError")
    try:
        binding = read_binding(resolve_path(payload_cwd, session_id))
    except binding_error:
        return None
    if binding is None or binding.session_id != session_id:
        return None
    parsed = json.loads(binding.to_json())
    return parsed if isinstance(parsed, dict) else None


def session_join_required(payload_cwd: str, session_id: str) -> bool:
    """Return whether the payload-identified binding requires a fixed-set join."""
    binding = read_session_binding(payload_cwd, session_id)
    return binding is not None and bool(binding.get("join_required", False))


def session_managed_scope(payload_cwd: str, session_id: str) -> tuple[str, str] | None:
    """Return the binding-authoritative parent/leaf scope for join guards.

    A join-bearing binding without a valid scope is deliberately not repaired
    from ambient values.  Callers that already established join applicability
    must deny rather than substitute the former ``top_level`` literal.
    """
    binding = read_session_binding(payload_cwd, session_id)
    if binding is None or not binding.get("join_required") or not binding.get("binding_valid"):
        return None
    parent = binding.get("managed_parent_id")
    leaf = binding.get("managed_leaf_id")
    if not isinstance(parent, str) or not parent or not isinstance(leaf, str):
        return None
    return (parent, leaf)


def session_managed_codex_route(
    payload_cwd: str,
    session_id: str,
) -> tuple[str, frozenset[str], str] | None:
    """Return the explicit managed Codex route carried by a valid binding."""
    binding = read_session_binding(payload_cwd, session_id)
    if binding is None or not binding.get("binding_valid"):
        return None
    route = binding.get("managed_route")
    guards = binding.get("managed_guard_set")
    config_digest = binding.get("managed_config_digest")
    if (
        route not in ("parent", "leaf")
        or not isinstance(guards, list)
        or any(not isinstance(guard, str) or not guard for guard in guards)
        or len(set(guards)) != len(guards)
        or not isinstance(config_digest, str)
        or not config_digest
    ):
        return None
    return str(route), frozenset(guards), config_digest
