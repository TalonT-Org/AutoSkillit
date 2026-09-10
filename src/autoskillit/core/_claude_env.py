"""Backward-compat shim for _claude_env. Real module at autoskillit.core.claude_env.claude_env."""

# ``os`` is re-exported but deliberately absent from ``__all__``. It was an
# incidental module global of the pre-move ``_claude_env.py`` (which declared no
# ``__all__``), and tests patch ``_claude_env.os.name`` to exercise the Windows
# branch of ``build_maintenance_env``. Keeping the import preserves that
# attribute surface; listing it in ``__all__`` would wrongly advertise the
# stdlib module as part of this shim's public API.
from autoskillit.core.claude_env.claude_env import (
    IDE_ENV_ALWAYS_EXTRAS,
    IDE_ENV_DENYLIST,
    IDE_ENV_PREFIX_DENYLIST,
    build_agent_env,
    build_claude_env,
    build_maintenance_env,
    os,  # noqa: F401
    resolve_dbus_session_bus_address,
)

__all__ = [
    "IDE_ENV_ALWAYS_EXTRAS",
    "IDE_ENV_DENYLIST",
    "IDE_ENV_PREFIX_DENYLIST",
    "build_agent_env",
    "build_claude_env",
    "build_maintenance_env",
    "resolve_dbus_session_bus_address",
]
