"""Canonical env builder for claude-launching subprocesses.

Every subprocess that invokes the ``claude`` CLI must route its environment
through :func:`build_claude_env` so that host-process IDE state (VS Code,
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
   is always injected. This suppresses the ``~/.claude/ide/*.lock`` scan
   fallback that the Claude CLI follows at startup even when no IDE env
   vars are set; without it, third-party IDE bridges (e.g.
   ``claudecode.nvim``) can still attach via the lock-file mechanism.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from types import MappingProxyType

from .types._type_constants import AUTOSKILLIT_PRIVATE_ENV_VARS

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
        "SCENARIO_STEP_NAME",
        # MCP response size gate: injected explicitly by AutoSkillit session launchers
        # so the child always gets the correct value regardless of the parent env.
        "MAX_MCP_OUTPUT_TOKENS",
    }
)

# Prefix-match IDE variable patterns stripped from the child env.
IDE_ENV_PREFIX_DENYLIST: tuple[str, ...] = (
    "CLAUDE_CODE_IDE_",
    "CLAUDE_CODE_SSE",
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


def build_claude_env(
    base: Mapping[str, str] | None = None,
    *,
    extras: Mapping[str, str] | None = None,
) -> Mapping[str, str]:
    """Return a scrubbed, sealed env dict suitable for a claude subprocess.

    Parameters
    ----------
    base
        Starting environment. Defaults to ``os.environ``.
    extras
        Caller-supplied overrides merged last. Used to carry
        ``AUTOSKILLIT_HEADLESS=1``, ``SCENARIO_STEP_NAME=...`` and similar
        into the child.

    Returns
    -------
    Mapping[str, str]
        A ``MappingProxyType`` over the resolved env. Both
        ``subprocess.run(env=...)`` and ``anyio.open_process(env=...)``
        accept any ``Mapping``, so this is a drop-in for the underlying
        runners. The read-only view prevents post-build mutation.
    """
    src = os.environ if base is None else base
    out: dict[str, str] = {
        k: v
        for k, v in src.items()
        if k not in IDE_ENV_DENYLIST
        and k not in AUTOSKILLIT_PRIVATE_ENV_VARS
        and not any(k.startswith(p) for p in IDE_ENV_PREFIX_DENYLIST)
    }
    out.update(IDE_ENV_ALWAYS_EXTRAS)
    if extras:
        out.update(extras)
    return MappingProxyType(out)
