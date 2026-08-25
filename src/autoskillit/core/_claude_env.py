"""Canonical env builder for agent subprocesses.

Every subprocess that invokes the `claude` CLI must route its environment
through :func:`build_agent_env` so that host-process IDE state (VS Code,
Cursor, Zed, JetBrains, Neovim bridges) cannot leak across the trust
boundary and silently widen the child's tool surface.

Three layers of immunity are applied:

1. **Denylist scrub** — IDE discovery variables such as
   ``CLAUDE_CODE_SSE_PORT`` and the ``CLAUDE_CODE_IDE_*`` family are
   stripped from ``base``. Removing the port env closes the direct-signal
   attach path.
2. **Private var scrub** — AutoSkillit internal orchestration variables
   listed in ``AUTOSKILLIT_PRIVATE_ENV_VARS`` (e.g. ``AUTOSKILLIT_SESSION_TYPE``,
   ``AUTOSKILLIT_CAMPAIGN_ID``) are stripped so parent session state cannot
   leak into child sessions. Callers opt back in via ``extras``.
3. **Implicit auto-connect disable** — ``CLAUDE_CODE_AUTO_CONNECT_IDE=0``
   is always injected. This suppresses the `~/.claude/ide/*.lock` scan
   fallback that the Claude CLI follows at startup even when no IDE env
   vars are set; without it, third-party IDE bridges (e.g.
   ``claudecode.nvim``) can still attach via the lock-file mechanism.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from types import MappingProxyType

from .types._type_constants_env import (
    AUTOSKILLIT_ATTESTED_CLIENT_GATE_TOKENS,
    AUTOSKILLIT_ATTESTED_META_SUPPORT,
    AUTOSKILLIT_PRIVATE_ENV_VARS,
)

# Exact-match IDE discovery variable names stripped from the child env.
IDE_ENV_DENYLIST: frozenset[str] = frozenset(
    {
        "CLAUDE_CODE_SSE_PORT",
        "ENABLE_IDE_INTEGRATION",
        "CLAUDE_CODE_WEBSOCKET_AUTH_FILE_DESCRIPTOR",
        "VSCODE_GIT_ASKPASS_MAIN",
        "CURSOR_TRACE_ID",
        "ZED_TERM",
        # Session-lifetime vars: stripped from base so callers control them explicitly
        # via extras. Without stripping, a parent headless session's env would leak
        # into child sessions even when exit_after_stop_delay_ms=0 or no step name.
        "CLAUDE_CODE_EXIT_AFTER_STOP_DELAY",
        "CLAUDE_STREAM_IDLE_TIMEOUT_MS",
        "SCENARIO_STEP_NAME",
        # MCP response size gate: injected explicitly by AutoSkillit session launchers
        # so the child always gets the correct value regardless of the parent env.
        "MAX_MCP_OUTPUT_TOKENS",
        # Host client attestation: launcher-injected (SHARED_BASELINE_ENV), never
        # IDE-sourced. Stripped from base so a parent session's attestation cannot
        # leak into a child that the launcher did not itself attest.
        AUTOSKILLIT_ATTESTED_CLIENT_GATE_TOKENS,
        AUTOSKILLIT_ATTESTED_META_SUPPORT,
        # Skill-session hardening: _CLAUDE_SKILL_SESSION_HARDENING in
        # _claude_prompt.py force-sets these for skill sessions only.
        # build_interactive_cmd/build_resume_cmd don't apply that hardening,
        # so stripped from base here to stop a host-inherited value leaking
        # into those non-skill launch paths.
        "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS",
        "CLAUDE_CODE_DISABLE_CRON",
        # Per-MCP-tool-call idle-abort timeout: only overridden when the caller
        # passes mcp_tool_timeout_sec > 0. Stripped from base so an unset caller
        # value doesn't let a host-inherited timeout leak into the child.
        "CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT",
    }
)

# Prefix-match IDE variable patterns stripped from the child env.
IDE_ENV_PREFIX_DENYLIST: tuple[str, ...] = (
    "CLAUDE_CODE_IDE_",
    "CLAUDE_CODE_SSE",
    "CLAUDE_CODE_SUBAGENT_",
)

# Variables injected into every built env regardless of caller. These close
# discovery paths that cannot be closed by scrubbing alone — notably
# CLAUDE_CODE_AUTO_CONNECT_IDE=0, which suppresses the ~/.claude/ide/*.lock
# scan path that fires even when SSE_PORT is absent.
IDE_ENV_ALWAYS_EXTRAS: Mapping[str, str] = MappingProxyType(
    {
        "CLAUDE_CODE_AUTO_CONNECT_IDE": "0",
    }
)

# Maintenance subprocesses start from an empty environment and admit only the
# host values needed to locate executables, user-scoped package configuration,
# network/certificate configuration, and basic process facilities.
_MAINTENANCE_BASE_ENV_KEYS: frozenset[str] = frozenset(
    {
        "HOME",
        "PATH",
        "XDG_CONFIG_HOME",
        "XDG_CACHE_HOME",
        "XDG_DATA_HOME",
        "XDG_STATE_HOME",
        "XDG_RUNTIME_DIR",
        "USER",
        "LOGNAME",
        "SHELL",
        "TERM",
        "TMPDIR",
        "TEMP",
        "TMP",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "no_proxy",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
        "PIP_CERT",
        "PIP_INDEX_URL",
        "PIP_EXTRA_INDEX_URL",
        "PIP_TRUSTED_HOST",
        "UV_INDEX_URL",
        "UV_EXTRA_INDEX_URL",
        "UV_DEFAULT_INDEX",
        "UV_PYTHON_INSTALL_DIR",
        "UV_CACHE_DIR",
    }
)
_MAINTENANCE_WINDOWS_BASE_ENV_KEYS: frozenset[str] = frozenset(
    {
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "PATHEXT",
    }
)
_MAINTENANCE_EXTRA_KEYS: frozenset[str] = frozenset(
    {
        "AUTOSKILLIT_SKIP_STALE_CHECK",
        "AUTOSKILLIT_SKIP_UPDATE_CHECK",
    }
)


def resolve_dbus_session_bus_address(base_env: Mapping[str, str] | None = None) -> str:
    """Forward the host's DBUS_SESSION_BUS_ADDRESS, or explicitly disable autolaunch.

    libdbus attempts autolaunch (dbus-launch -> dbus-daemon --fork) only when
    DBUS_SESSION_BUS_ADDRESS is *unset* -- the mechanism that stops the leak is the
    variable being defined at all, not the value being reachable. A conditional-forward
    rule ("only set it when the host has one") is a no-op in exactly the headless/
    container environments that leak, so every child environment builder must call this
    and set the result unconditionally. "disabled:" is not a specified transport -- it is
    the sentinel Chromium introduced (2017, CL 2861163002) to stop libdbus's nested
    fork()/exec() autolaunch; jeepney's address parser (this repo's dbus client) raises
    ValueError on it before ever reaching a connect attempt, which is what actually
    prevents the daemon spawn.
    """
    source = os.environ if base_env is None else base_env
    return source.get("DBUS_SESSION_BUS_ADDRESS") or "disabled:"


def _is_protected_maintenance_extra(key: str) -> bool:
    upper_key = key.upper()
    return (
        upper_key.startswith(("CLAUDECODE", "CODEX", "PYTHON", "LD_", "DYLD_"))
        or upper_key.startswith(("VIRTUAL_ENV", "CONDA"))
        or upper_key.startswith(("PIP_", "UV_", "POETRY_", "PDM_", "PIPENV_"))
        or (
            upper_key.startswith("AUTOSKILLIT_")
            and any(family in upper_key for family in ("BACKEND", "SESSION", "CAMPAIGN", "ORDER"))
        )
    )


def build_maintenance_env(
    base_env: Mapping[str, str],
    extras: Mapping[str, str] | None = None,
) -> Mapping[str, str]:
    """Return a minimal sealed environment for update/install subprocesses.

    Host values are copied from an explicit allowlist; the environment is
    never cloned wholesale. Callers may add only the two maintenance recursion
    guards. Rejection messages name offending keys without exposing values.
    """
    supplied_extras = {} if extras is None else extras
    protected = sorted(key for key in supplied_extras if _is_protected_maintenance_extra(key))
    if protected:
        raise ValueError(f"Protected maintenance env extras are not allowed: {protected}")

    unsupported = sorted(set(supplied_extras) - _MAINTENANCE_EXTRA_KEYS)
    if unsupported:
        raise ValueError(f"Unsupported maintenance env extras: {unsupported}")

    allowed_base_keys = _MAINTENANCE_BASE_ENV_KEYS
    if os.name == "nt":
        allowed_base_keys = allowed_base_keys | _MAINTENANCE_WINDOWS_BASE_ENV_KEYS
    out = {key: base_env[key] for key in sorted(allowed_base_keys) if key in base_env}
    # Unconditional, not allowlist-forwarded: build_maintenance_env only copies a base
    # key when it's already present in base_env, but DBUS_SESSION_BUS_ADDRESS must be set
    # even when base_env lacks it -- an unset var is exactly what triggers dbus autolaunch.
    out["DBUS_SESSION_BUS_ADDRESS"] = resolve_dbus_session_bus_address(base_env)
    out.update(supplied_extras)
    return MappingProxyType(out)


def build_agent_env(
    base: Mapping[str, str] | None = None,
    *,
    extras: Mapping[str, str] | None = None,
    required: frozenset[str] | None = None,
) -> Mapping[str, str]:
    """Return a scrubbed, sealed env dict suitable for an agent subprocess.

    Parameters
    ----------
    base
        Starting environment. Defaults to ``os.environ``.
    extras
        Caller-supplied overrides merged last. Used to carry
        ``AUTOSKILLIT_HEADLESS=1``, ``SCENARIO_STEP_NAME=...`` and similar
        into the child.
    required
        When provided, raise ``ValueError`` if any key in this set is absent
        from the final assembled env. Used by session launchers to enforce
        that required vars were explicitly injected and not silently scrubbed.

    Returns
    -------
    Mapping[str, str]
        A ``MappingProxyType`` over the resolved env. The read-only view
        prevents post-build mutation. Callers that pass the result to
        external subprocess APIs must coerce to ``dict`` at the boundary
        (uvloop requires ``type(env) is dict``).
    """
    src = os.environ if base is None else base
    out: dict[str, str] = {
        k: v
        for k, v in src.items()
        if k not in IDE_ENV_DENYLIST
        and k not in AUTOSKILLIT_PRIVATE_ENV_VARS
        and not any(k.startswith(p) for p in IDE_ENV_PREFIX_DENYLIST)
    }
    out["DBUS_SESSION_BUS_ADDRESS"] = resolve_dbus_session_bus_address(src)
    out.update(IDE_ENV_ALWAYS_EXTRAS)
    if extras:
        out.update(extras)
    _session_type_raw = out.get("AUTOSKILLIT_SESSION_TYPE")
    if _session_type_raw:
        from .types._type_enums import SessionType

        try:
            SessionType(_session_type_raw)
        except ValueError:
            valid = ", ".join(m.value for m in SessionType)
            raise ValueError(
                f"AUTOSKILLIT_SESSION_TYPE={_session_type_raw!r} is not a valid SessionType. "
                f"Valid values: {valid}"
            ) from None
    if required is not None:
        missing = required - frozenset(out.keys())
        if missing:
            raise ValueError(f"Required env vars missing from session env: {sorted(missing)}")
    return MappingProxyType(out)


build_claude_env = build_agent_env
