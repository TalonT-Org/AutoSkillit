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
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from autoskillit.hooks._session_binding import JoinAdmission

# Keep in sync with _HOOK_CONFIG_PATH_COMPONENTS in hooks/_fmt_primitives.py
# (stdlib-only boundary prevents a shared import).
HOOK_CONFIG_FILENAME = ".hook_config.json"
HOOK_CONFIG_OVERLAY_FILENAME = ".hook_config_overlay.json"
HOOK_DIR_COMPONENTS = (".autoskillit", "temp")

DEFAULT_CACHE_PATH = "~/.claude/autoskillit_quota_cache.json"
DEFAULT_CACHE_MAX_AGE = 300

ENV_CACHE_PATH = "AUTOSKILLIT_QUOTA_GUARD__CACHE_PATH"
ENV_CACHE_MAX_AGE = "AUTOSKILLIT_QUOTA_GUARD__CACHE_MAX_AGE"
ENV_DISABLED = "AUTOSKILLIT_QUOTA_GUARD__DISABLED"

# The exact keys this module reads from hook_config["quota_guard"].
# The bridge contract test asserts equality between this set and the
# serializer's payload keys — update both together.
QUOTA_GUARD_HOOK_PAYLOAD_KEYS: frozenset[str] = frozenset(
    {
        "cache_path",
        "cache_max_age",
        "disabled",
        "quota_account_scope",
    }
)

# The exact keys the descriptor-anchored shell capture runner reads from
# hook_config["output_budget_policy"]. Keep this stdlib-only declaration in
# sync with _output_budget_policy_hook_payload() in
# server/tools/tools_kitchen/_hook_config.py.
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
        "backend",
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
        "turn_usage_file",
        "turn_usage_count",
        "turn_usage_schema_version",
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


@dataclass(frozen=True, slots=True, kw_only=True)
class QuotaHookSettings:
    """Resolved settings for quota guard hooks.

    Marked ``kw_only`` to defend against positional construction silently
    re-enabling the ``disabled`` flag when callers insert fields before it.
    Keyword-only fields prevent positional construction from silently binding
    a quota account scope as the ``disabled`` flag.
    """

    cache_path: str
    cache_max_age: int
    quota_account_scope: str = ""
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
    """Atomic write for a small JSON marker.

    Re-exported from ``_hook_log_dispatch`` so the quota-disable marker
    below can use the same atomic-write helper as the JSONL sinks.
    """
    import _hook_log_dispatch  # type: ignore[import-not-found]  # noqa: PLC0415

    _hook_log_dispatch._atomic_write_marker(marker_path, payload)


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
    ``cache_max_age``: env var > hook config > default.
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
        quota_account_scope=str(hook_config.get("quota_account_scope", "")),
        disabled=disabled,
    )


_AUTOSKILLIT_LOG_DIR_ENV = "AUTOSKILLIT_LOG_DIR"
# Mirror of _hook_constants.MANAGED_JOIN_PARENT_ID_ENV_VAR. The hook-settings
# bridge is loaded as a bare script by hook subprocesses (no parent package),
# so relative imports fail; tests/hooks/test_hook_constants_authority.py
# enforces parity with the canonical constant.
_AUTOSKILLIT_MANAGED_JOIN_PARENT_ID_ENV = "AUTOSKILLIT_MANAGED_JOIN_PARENT_ID"
_AUTOSKILLIT_LAUNCH_ID_ENV = "AUTOSKILLIT_LAUNCH_ID"
_AUTOSKILLIT_AGENT_BACKEND_ENV = "AUTOSKILLIT_AGENT_BACKEND"


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
    Mirrors the logic in execution/session_log/session_log.py:resolve_log_dir().
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


# Re-export the JSONL sinks from ``_hook_log_dispatch`` so callers of
# ``_hook_settings.write_join_diagnostic`` (and friends) continue to work
# after the decomposition. The actual definitions live in
# ``_hook_log_dispatch`` to keep this module under the REQ-CNST-010
# 750 non-import line hard cap.
from _hook_log_dispatch import (  # type: ignore[import-not-found]  # noqa: E402,PLC0415,F401
    write_dispatch_diagnostic,
    write_join_diagnostic,
    write_quota_log_event,
)


def hook_session_shape() -> tuple[bool, str]:
    """Return the normalized shape without rejecting an unknown hook tier."""
    headless = os.environ.get("AUTOSKILLIT_HEADLESS") == "1"
    # Empty-string short-circuit: an explicitly-empty AUTOSKILLIT_SESSION_TYPE
    # must default to "skill" identically to the unset case. Pinned by
    # tests/hooks/test_session_shape_parity.py::test_canonical_accessor_is_hook_session_shape.
    tier = os.environ.get("AUTOSKILLIT_SESSION_TYPE", "").lower() or "skill"
    return headless, tier


def admit_hook_session_scope(
    session_scope: str,
    exempt_tiers: frozenset[str],
    shape: tuple[bool, str],
) -> bool:
    """Return whether a HookDef scope admits a raw hook-process shape."""
    # Module-reference import so monkeypatching
    # _session_scope_authority.SESSION_SCOPE_VALUES takes effect on every call.
    # The bare-name form (rather than `from autoskillit.hooks._runtime...`) is
    # intentional — it matches the subprocess sys.path bootstrap that resolves
    # this module against hooks/_runtime/ before the package import graph is
    # fully built. A function-local import (rather than module top) keeps the
    # cycle contained to admit_hook_session_scope's lookup site.
    import _session_scope_authority as _ssa

    if session_scope not in _ssa.SESSION_SCOPE_VALUES:
        msg = f"Unknown hook session scope: {session_scope!r}"
        raise ValueError(msg)
    headless, tier = shape
    if session_scope == "headless_only" and not headless:
        return False
    if session_scope == "interactive_only" and headless:
        return False
    return tier not in exempt_tiers


def enforce_session_scope(
    session_scope: str,
    *,
    exempt_tiers: frozenset[str] = frozenset(),
) -> None:
    """Exit successfully when a hook is outside its declared session scope."""
    shape = hook_session_shape()
    if shape[1] not in {"skill", "orchestrator", "fleet"}:
        write_dispatch_diagnostic("invalid_session_shape", "enforce_session_scope", shape[1])
    if not admit_hook_session_scope(session_scope, exempt_tiers, shape):
        raise SystemExit(0)


def session_join_admission(payload_cwd: str, session_id: str) -> "JoinAdmission":
    """Return the authoritative join decision for the payload session."""
    module_name = (
        f"{__package__.rsplit('.', 1)[0]}._session_binding" if __package__ else "_session_binding"
    )
    binding_module = importlib.import_module(module_name)
    return getattr(binding_module, "admit_join")(
        getattr(binding_module, "resolve_binding_path")(payload_cwd, session_id),
        session_id=session_id,
        skill_name="",
    )


_REGISTRY_BRIDGE_MODULE = (
    f"{__package__}._session_registry_bridge" if __package__ else "_session_registry_bridge"
)


def _registry_bridge_call(name: str, *args: object, **kwargs: object) -> object:
    """Dispatch ``name`` on the registry-bridge module (centralized importlib dance)."""
    return getattr(importlib.import_module(_REGISTRY_BRIDGE_MODULE), name)(*args, **kwargs)


def _cook_registry_predicate(name: str, *args: object) -> bool:
    return bool(
        _registry_bridge_call(
            name,
            *args,
            headless=hook_session_shape()[0],
            backend=os.environ.get(_AUTOSKILLIT_AGENT_BACKEND_ENV, "").strip(),
            launch_id=os.environ.get(_AUTOSKILLIT_LAUNCH_ID_ENV, ""),
            managed_parent_id=os.environ.get(_AUTOSKILLIT_MANAGED_JOIN_PARENT_ID_ENV, ""),
        )
    )


def is_authenticated_top_level_cook(
    payload: dict[str, object], payload_cwd: str, binding_session_id: str
) -> bool:
    """Apply payload identity to the authenticated cook session."""
    return _cook_registry_predicate(
        "is_authenticated_top_level_cook", payload, payload_cwd, binding_session_id
    )


def is_authenticated_top_level_cook_session(payload_cwd: str, binding_session_id: str) -> bool:
    """Check cook identity from the current session's launch environment."""
    return _cook_registry_predicate(
        "is_authenticated_top_level_cook_session", payload_cwd, binding_session_id
    )


def bridge_session_registry(session_id: str, payload_cwd: str = "") -> None:
    """Bind the hook session through the canonical launch-id accessor."""
    _registry_bridge_call(
        "bridge_session_registry",
        session_id,
        payload_cwd,
        launch_id=os.environ.get(_AUTOSKILLIT_LAUNCH_ID_ENV, ""),
    )


def session_managed_scope(payload_cwd: str, session_id: str) -> tuple[str, str] | None:
    """Return a valid binding-authoritative parent/leaf scope for join guards.

    Deliberately not repaired from ambient values: callers that already
    established join applicability must deny rather than substitute the
    former top_level literal — returning ``None`` forces a deterministic
    denial reason instead of silently widening enforcement.
    """
    admission = session_join_admission(payload_cwd, session_id)
    binding = admission.binding_dict
    if binding is None or not admission.enforce or not binding.get("binding_valid"):
        return None
    parent = binding.get("managed_parent_id")
    leaf = binding.get("managed_leaf_id")
    if not isinstance(parent, str) or not parent or not isinstance(leaf, str):
        return None
    return (parent, leaf)


def _write_cook_bypass_diagnostic(payload_cwd: str, session_id: str, *, gate: str) -> None:
    managed_parent_id, managed_leaf_id = session_managed_scope(payload_cwd, session_id) or ("", "")
    write_join_diagnostic(
        {
            "gate": gate,
            "status": "cook_bypass",
            "session_id": session_id,
            "managed_parent_id": managed_parent_id,
            "managed_leaf_id": managed_leaf_id,
        },
        caller=gate,
    )


def record_session_cook_join_bypass(payload_cwd: str, session_id: str, *, gate: str) -> bool:
    """Record MCP cook bypass from the per-client stdio child's launch env.
    Restricted child environments omit the launch ID and keep declaration enforced."""
    if not is_authenticated_top_level_cook_session(payload_cwd, session_id):
        return False
    _write_cook_bypass_diagnostic(payload_cwd, session_id, gate=gate)
    return True


class JoinApplicability(NamedTuple):
    cook_bypass: bool
    admission: "JoinAdmission | None"

    @property
    def enforce(self) -> bool:
        return not self.cook_bypass and self.admission is not None and self.admission.enforce


def hook_join_applicability(
    payload: dict[str, object], payload_cwd: str, session_id: str, *, gate: str
) -> JoinApplicability:
    """Decide cook bypass before consulting the session's join admission."""
    if is_authenticated_top_level_cook(payload, payload_cwd, session_id):
        _write_cook_bypass_diagnostic(payload_cwd, session_id, gate=gate)
        return JoinApplicability(True, None)
    return JoinApplicability(False, session_join_admission(payload_cwd, session_id))


def session_managed_codex_route(
    payload_cwd: str,
    session_id: str,
) -> tuple[str, frozenset[str], str] | None:
    """Return the explicit managed Codex route carried by a valid binding."""
    admission = session_join_admission(payload_cwd, session_id)
    binding = admission.binding_dict
    if binding is None or not admission.enforce or not binding.get("binding_valid"):
        return None
    route = binding.get("managed_route")
    guards = binding.get("managed_guard_set")
    config_digest = binding.get("managed_config_digest")
    if (
        route not in ("parent", "leaf", "interactive-parent")
        or not isinstance(guards, list)
        or any(not isinstance(guard, str) or not guard for guard in guards)
        or len(set(guards)) != len(guards)
        or not isinstance(config_digest, str)
        or not config_digest
    ):
        return None
    return str(route), frozenset(guards), config_digest


def resolve_binding_session_id(payload: dict[str, object]) -> str:
    """Prefer the managed join identity delivered to a Codex hook process."""
    managed_parent_id = os.environ.get(_AUTOSKILLIT_MANAGED_JOIN_PARENT_ID_ENV, "")
    if managed_parent_id:
        return managed_parent_id
    session_id = payload.get("session_id", "")
    return session_id if isinstance(session_id, str) else ""


def payload_managed_codex_route(
    payload_cwd: str,
    session_id: object,
) -> tuple[str, frozenset[str], str] | None:
    """Resolve a managed Codex route only for an identified Codex payload."""
    if os.environ.get("AUTOSKILLIT_AGENT_BACKEND", "").strip() != "codex":
        return None
    if not isinstance(session_id, str) or not session_id or not payload_cwd:
        return None
    return session_managed_codex_route(payload_cwd, session_id)
